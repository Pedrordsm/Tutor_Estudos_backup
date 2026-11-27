"""Lógica completa do Módulo 4: Time Agno (Planejador, Professor, Avaliador) e extração verificada."""

import json
import uuid
from typing import Callable, Optional, TypeVar, Union

from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.models.base import Model
from agno.team import Team
from pydantic import ValidationError

from carreira_contratos import Curriculo, Exercicio, Lacunas, PlanoEstudos, RegistroProgresso
from carreira_contratos.modelos import TipoExercicio

from .calculos import MODULO, montar_plano, montar_progresso
from .fidelidade import verificar_plano, verificar_progresso
from .modelos import AvaliacaoExercicio, RascunhoPlano, RascunhoProgresso
from .sandbox import (
    executar_codigo_python,
    executar_python_isolado,
    executar_sql_em_memoria,
)

ID_TIME_TUTOR = "tutor-estudos-time"
ID_AGENTE_PLANEJADOR = "tutor-planejador"
ID_AGENTE_PROFESSOR = "tutor-professor"
ID_AGENTE_AVALIADOR = "tutor-avaliador"

REGRAS_GERAIS = [
    "Responda sempre em português do Brasil, com clareza e rigor técnico.",
    "Use apenas as lacunas e o currículo presentes no estado da sessão; nunca atribua ao formando conhecimentos, experiências ou resultados que ele não demonstrou.",
    "Uma habilidade só pode ser considerada adquirida quando houver evidência concreta registrada (exercícios corrigidos corretamente ou projeto concluído).",
    "Se não tiver certeza sobre algum detalhe técnico, diga explicitamente ao formando em vez de inventar.",
]

T = TypeVar("T")


class ErroFidelidade(RuntimeError):
    def __init__(self, problemas: list[str]):
        super().__init__("; ".join(problemas))
        self.problemas = problemas


class SessaoInexistente(KeyError):
    pass


def _contexto(lacunas: Lacunas, curriculo: Curriculo) -> str:
    return "\n".join([
        "Lacunas do formando (JSON):",
        lacunas.model_dump_json(exclude={"cabecalho"}),
        "Currículo do formando (JSON):",
        curriculo.model_dump_json(exclude={"cabecalho"}),
    ])


class ServicoTutor:
    def __init__(
        self,
        modelo: Union[Model, str],
        db: SqliteDb,
        telemetria: bool = False,
        tentativas_fidelidade: int = 2,
        timeout_sandbox: int = 5,
    ):
        self.tentativas_fidelidade = tentativas_fidelidade
        self.timeout_sandbox = timeout_sandbox

        # retries + exponential_backoff tratam HTTP 429 e falhas transitórias do provedor
        comum = dict(
            model=modelo,
            telemetry=telemetria,
            retries=3,
            delay_between_retries=5,
            exponential_backoff=True,
        )

        # ---------------------------------------- 3 agentes especializados (Caderno M4, seção 7)

        self.planejador = Agent(
            id=ID_AGENTE_PLANEJADOR,
            name="Agente Planejador",
            role="Conduz o diagnóstico inicial, monta o plano de estudos priorizado com pré-requisitos e adapta o plano ao desempenho.",
            instructions=[
                "F1 (Diagnóstico): comece perguntando a disponibilidade semanal em horas e faça perguntas rápidas para confirmar o nível atual nas lacunas.",
                "F2 (Plano): crie tópicos apenas para habilidades listadas em `lacunas`, priorizando prioridade alta, respeitando pré-requisitos e definindo objetivos, atividades e carga horária realista.",
                "F5 (Adaptação): quando o formando tiver baixo desempenho (< 70%) em um tópico, proponha uma sessão extra de revisão e registre o ajuste; quando dominar rápido, permita avançar.",
                "F6 (Projetos práticos): sugira projetos práticos que gerem evidências reais para o portfólio/currículo.",
            ] + REGRAS_GERAIS,
            **comum,
        )

        self.professor = Agent(
            id=ID_AGENTE_PROFESSOR,
            name="Agente Professor",
            role="Dá aulas didáticas em sessões sobre os tópicos do plano, com exemplos, analogias e trechos de código.",
            instructions=[
                "F3 (Aulas em sessões): explique o tópico em linguagem adequada ao nível atual do formando (do currículo/lacunas), com exemplos concretos, analogias e trechos de código quando couber.",
                "F6 (Projetos práticos): relacione a explicação com situações reais de mercado e projetos de portfólio.",
                "Mantenha as explicações organizadas e termine propondo avançar para um exercício prático com o Avaliador.",
            ] + REGRAS_GERAIS,
            **comum,
        )

        self.avaliador = Agent(
            id=ID_AGENTE_AVALIADOR,
            name="Agente Avaliador",
            role="Aplica exercícios (objetivos, discursivos e de código), executa código na sandbox isolada, corrige com feedback e registra evidências.",
            instructions=[
                "F4 (Exercícios e correção): proponha exercícios variados (objetiva, discursiva ou codigo) alinhados ao tópico atual.",
                "Para exercícios de código Python, use SEMPRE a tool `executar_codigo_python` com testes reais (asserts ou função testada) antes de dar o veredito.",
                "Para exercícios de consulta SQL, use a tool `executar_sql_em_memoria` criando tabelas de teste em memória para validar a consulta.",
                "F7 (Progresso com evidências): diga explicitamente se a resposta está correta ou incorreta, dê feedback construtivo e só indique habilidade adquirida quando houver evidência comprovada.",
            ] + REGRAS_GERAIS,
            tools=[executar_codigo_python, executar_sql_em_memoria],
            **comum,
        )

        # ---------------------------------------- Time coordenador com memória em SQLite (F8)

        self.time = Team(
            id=ID_TIME_TUTOR,
            name="Tutor de estudos",
            members=[self.planejador, self.professor, self.avaliador],
            db=db,
            description=(
                "Time de tutoria do Módulo 4 (Planejador, Professor e Avaliador) que transforma "
                "as lacunas do formando em plano de estudos, aulas, exercícios corrigidos e registro de progresso."
            ),
            instructions=[
                "Você coordena um time de 3 especialistas: Agente Planejador (diagnóstico, plano e adaptações), "
                "Agente Professor (aulas e exemplos) e Agente Avaliador (exercícios e correção com sandbox).",
                "Use as lacunas e o currículo salvos no estado da sessão (`session_state`).",
                "Delegue cada pedido ao especialista adequado: diagnóstico/plano/replanejamento → Planejador; "
                "explicação de conteúdo → Professor; proposição/correção de exercício → Avaliador.",
                "Na retomada de sessão (F8), relembre brevemente onde o formando parou com base no histórico.",
            ] + REGRAS_GERAIS,
            add_session_state_to_context=True,
            add_history_to_context=True,
            num_history_runs=15,
            add_team_history_to_members=True,
            share_member_interactions=True,
            **comum,
        )

        # ---------------------------------------- Extratores estruturados + avaliador avulso

        self.extrator_plano = Agent(
            name="Extrator do plano de estudos",
            instructions=[
                "Leia as lacunas, o currículo e a conversa da tutoria e extraia o RascunhoPlano estruturado.",
                "REGRA CRÍTICA para `topicos[].habilidade`: use EXATAMENTE o nome curto da habilidade como aparece em `lacunas.itens[].habilidade` (por exemplo: \"Power BI\" ou \"Apache Airflow\"). NUNCA coloque frases, verbos ou objetivos no campo `habilidade` — frases de aprendizagem vão em `objetivos` e `titulo`.",
                "Garanta que TODA lacuna com `prioridade == 'alta'` tenha pelo menos um tópico com `habilidade` exatamente igual ao nome da lacuna.",
                "Informe em `horas_semanais` a disponibilidade semanal do formando (ex.: 6.0), e NUNCA a soma das horas dos tópicos.",
                "Informe em `horas_estimadas` de CADA tópico um número positivo maior que zero (ex.: 4.0, 6.0 ou 8.0; nunca 0).",
                "Cada tópico deve ter `objetivos` e/ou `atividades` preenchidos; `prerequisitos` deve citar apenas títulos exatos de tópicos anteriores.",
                "Preencha `projeto_pratico` quando um projeto prático tiver sido sugerido para o tópico.",
                "Marque `situacao` (pendente, em_andamento ou concluido) conforme o andamento da conversa.",
            ] + REGRAS_GERAIS,
            output_schema=RascunhoPlano,
            **comum,
        )

        self.extrator_progresso = Agent(
            name="Extrator do registro de progresso",
            instructions=[
                "Leia as lacunas, o currículo e a conversa da tutoria e extraia o RascunhoProgresso estruturado.",
                "Registre em `sessoes` apenas aulas/exercícios efetivamente realizados na conversa (nunca coloque saudações ou despedidas como exercícios).",
                "Para cada exercício aplicado e respondido, preencha `enunciado`, `tipo` (objetiva, discursiva ou codigo), `resposta_formando`, `correto` (true/false) e `feedback`.",
                "Só inclua item em `habilidades_adquiridas` se houver exercício resolvido corretamente ou projeto concluído na conversa, citando essa evidência em `evidencias`; caso contrário deixe `habilidades_adquiridas` vazia.",
                "Registre em `ajustes_plano` qualquer revisão ou adaptação feita no plano.",
            ] + REGRAS_GERAIS,
            output_schema=RascunhoProgresso,
            **comum,
        )

        self.corretor_estruturado = Agent(
            name="Corretor estruturado de exercícios",
            instructions=[
                "Avalie a resposta do formando para o exercício informado e devolva `correto` (bool) e `feedback` objetivo.",
                "Se houver resultado da execução em sandbox isolada no prompt, baseie seu veredito nesse resultado.",
            ] + REGRAS_GERAIS,
            output_schema=AvaliacaoExercicio,
            **comum,
        )

    # ------------------------------------------------------------ geração verificada com retry

    def _gerar_verificado(
        self,
        agente: Agent,
        modelo_cls: type[T],
        prompt: str,
        validador: Callable[[T], list[str]],
    ) -> T:
        problemas: list[str] = []
        for _ in range(self.tentativas_fidelidade):
            entrada = (
                prompt
                if not problemas
                else prompt + "\n\nCorrija obrigatoriamente estes problemas da tentativa anterior:\n- " + "\n- ".join(problemas)
            )
            bruto = agente.run(entrada).content
            try:
                rascunho = bruto if isinstance(bruto, modelo_cls) else modelo_cls.model_validate_json(str(bruto))
            except ValidationError as e:
                problemas = [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()]
                continue
            problemas = validador(rascunho)
            if not problemas:
                return rascunho
        raise ErroFidelidade(problemas)

    # ------------------------------------------------------------ ciclo conversacional

    def iniciar(self, lacunas: Lacunas, curriculo: Curriculo) -> tuple[str, str]:
        id_sessao = str(uuid.uuid4())
        estado = {
            "lacunas": lacunas.model_dump(mode="json"),
            "curriculo": curriculo.model_dump(mode="json"),
        }
        saida = self.time.run(
            "Apresente-se brevemente como Tutor de Estudos, liste as lacunas encontradas para o formando, "
            "pergunte quantas horas por semana ele tem disponíveis e faça 2 perguntas rápidas de diagnóstico "
            "para confirmar o nível atual dele.",
            session_id=id_sessao,
            session_state=estado,
        )
        return id_sessao, str(saida.content)

    def conversar(self, id_sessao: str, mensagem: str) -> str:
        self._estado(id_sessao)
        saida = self.time.run(mensagem, session_id=id_sessao)
        return str(saida.content)

    def _estado(self, id_sessao: str) -> dict:
        if self.time.get_session(session_id=id_sessao) is None:
            raise SessaoInexistente(id_sessao)
        estado = self.time.get_session_state(session_id=id_sessao) or {}
        if "lacunas" not in estado or "curriculo" not in estado:
            raise SessaoInexistente(id_sessao)
        return estado

    def resultado(self, id_sessao: str) -> tuple[PlanoEstudos, RegistroProgresso]:
        estado = self._estado(id_sessao)
        lacunas = Lacunas.model_validate(estado["lacunas"])
        curriculo = Curriculo.model_validate(estado["curriculo"])
        id_formando = curriculo.cabecalho.id_formando

        conversa = [
            {"papel": m.role, "texto": m.content}
            for m in self.time.get_chat_history(session_id=id_sessao)
            if m.role in ("user", "assistant") and m.content
        ]
        prompt = _contexto(lacunas, curriculo) + "\n\nConversa (JSON):\n" + json.dumps(conversa, ensure_ascii=False)

        habilidades_permitidas = ", ".join(f'"{l.habilidade}"' for l in lacunas.itens)
        prompt_plano = (
            prompt
            + f"\n\nLembrete obrigatório: o campo `habilidade` de cada tópico deve ser EXATAMENTE um destes valores: {habilidades_permitidas}."
        )
        rascunho_plano = self._gerar_verificado(
            self.extrator_plano,
            RascunhoPlano,
            prompt_plano,
            lambda r: verificar_plano(r, lacunas),
        )
        plano = montar_plano(rascunho_plano, lacunas, id_formando)

        rascunho_progresso = self._gerar_verificado(
            self.extrator_progresso,
            RascunhoProgresso,
            prompt,
            verificar_progresso,
        )
        progresso = montar_progresso(rascunho_progresso, id_formando)

        return plano, progresso

    # ------------------------------------------------------------ avaliação unitária (útil para Fase 4)

    def avaliar_exercicio(
        self,
        enunciado: str,
        tipo: TipoExercicio,
        resposta_formando: str,
        testes: Optional[str] = None,
    ) -> Exercicio:
        """Avalia uma resposta individual (usando sandbox isolada quando há testes de código Python)."""
        if tipo == "codigo" and testes:
            res = executar_python_isolado(resposta_formando, testes, timeout=self.timeout_sandbox)
            feedback = "Todos os testes passaram na execução isolada." if res.passou else res.erro
            return Exercicio(
                enunciado=enunciado,
                tipo=tipo,
                resposta_formando=resposta_formando,
                correto=res.passou,
                feedback=feedback,
            )

        prompt = (
            f"Tipo: {tipo}\n"
            f"Enunciado: {enunciado}\n"
            f"Resposta do formando:\n{resposta_formando}\n"
        )
        bruto = self.corretor_estruturado.run(prompt).content
        aval = bruto if isinstance(bruto, AvaliacaoExercicio) else AvaliacaoExercicio.model_validate_json(str(bruto))
        return Exercicio(
            enunciado=enunciado,
            tipo=tipo,
            resposta_formando=resposta_formando,
            correto=aval.correto,
            feedback=aval.feedback,
        )
