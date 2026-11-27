"""Testes automatizados completos do Módulo 4 (sandbox, fidelidade, cálculos, Time Agno, rotas e CLI)."""

import json
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from carreira_contratos import Exercicio, PlanoEstudos, RegistroProgresso, salvar
from carreira_contratos.verificar import verificar as verificar_contrato
from tutor_estudos import cli
from tutor_estudos.calculos import calcular_data_conclusao, calcular_desempenho, montar_plano, montar_progresso
from tutor_estudos.fidelidade import normalizar_habilidades_plano, verificar_plano, verificar_progresso
from tutor_estudos.modelos import RascunhoPlano, RascunhoProgresso
from tutor_estudos.sandbox import executar_python_isolado, executar_sql_memoria
from tutor_estudos.servico import ErroFidelidade
from tutor_estudos.servidor import criar_agent_os

from conftest import json_rascunho_plano, json_rascunho_progresso


# --------------------------------------------------------------------- sandbox

def test_sandbox_python_asserts_e_timeout():
    # e14 (correto) e e15 (incorreto) dos insumos do kit
    testes = (
        "assert contar_vogais('Banana') == 3\n"
        "assert contar_vogais('') == 0\n"
        "assert contar_vogais('AEIOU') == 5"
    )
    ok = executar_python_isolado("def contar_vogais(t):\n    return sum(1 for c in t.lower() if c in 'aeiou')", testes)
    assert ok.passou is True

    falha = executar_python_isolado("def contar_vogais(t):\n    return sum(1 for c in t if c in 'aeiou')", testes)
    assert falha.passou is False and "AssertionError" in falha.erro

    # e16 (loop infinito) -> morto pelo timeout
    loop = executar_python_isolado("def fatorial(n):\n    while True:\n        n = n", "assert fatorial(5) == 120", timeout=1)
    assert loop.passou is False and loop.timeout is True


def test_sandbox_pytest_e12_e_e13():
    helper = "def soma(a, b):\n    return a + b"
    # e12: possui asserts válidos
    e12 = executar_python_isolado("def test_soma():\n    assert soma(2, 3) == 5\n    assert soma(-1, 1) == 0", helper)
    assert e12.passou is True

    # e13: sem assert -> não pode dar falso positivo
    e13 = executar_python_isolado("def test_soma():\n    soma(2, 3)\n    soma(-1, 1)", helper)
    assert e13.passou is False and "assert" in e13.erro.lower()


def test_sandbox_bloqueios_seguranca_e_sql():
    assert executar_python_isolado("import socket\nsocket.gethostname()").bloqueado is True
    assert executar_python_isolado("import os\nos.listdir('.')").bloqueado is True
    assert executar_python_isolado("open('segredo.txt', 'w')").bloqueado is True

    setup = (
        "CREATE TABLE clientes (id INT, nome TEXT);"
        "CREATE TABLE pedidos (id INT, cliente_id INT, valor REAL);"
        "INSERT INTO clientes VALUES (1, 'Ana'), (2, 'Bruno');"
        "INSERT INTO pedidos VALUES (1, 1, 100.0);"
    )
    # e07: LEFT JOIN + COALESCE inclui Bruno com 0
    res = executar_sql_memoria(
        "SELECT c.nome, COALESCE(SUM(p.valor), 0) AS total FROM clientes c LEFT JOIN pedidos p ON p.cliente_id = c.id GROUP BY c.id, c.nome ORDER BY c.id;",
        setup,
    )
    assert res.passou is True
    dados = json.loads(res.saida)
    assert dados["linhas"] == [["Ana", 100.0], ["Bruno", 0.0]]

    # Bloqueia comandos destrutivos na sandbox SQL
    assert executar_sql_memoria("DROP TABLE clientes;", setup).bloqueado is True


# --------------------------------------------------------- fidelidade e cálculos

def test_fidelidade_plano(lacunas_ficticias):
    ok = RascunhoPlano.model_validate_json(json_rascunho_plano())
    assert verificar_plano(ok, lacunas_ficticias) == []

    fora = RascunhoPlano.model_validate_json(json_rascunho_plano(habilidade_extra="Rust"))
    assert any("Rust" in p for p in verificar_plano(fora, lacunas_ficticias))

    sem_alta = RascunhoPlano.model_validate_json(json_rascunho_plano(incluir_power_bi=False))
    assert any("prioridade alta" in p for p in verificar_plano(sem_alta, lacunas_ficticias))

    pre_inv = RascunhoPlano.model_validate_json(json_rascunho_plano(prerequisito_invalido="Tópico Inexistente"))
    assert any("pré-requisito" in p for p in verificar_plano(pre_inv, lacunas_ficticias))

    irreal = RascunhoPlano.model_validate_json(json_rascunho_plano(horas_semanais=120.0))
    assert any("horas_semanais" in p for p in verificar_plano(irreal, lacunas_ficticias))

    # Nome descritivo inequívoco ("Modelagem com Power BI") normaliza para "Power BI";
    # habilidade inventada ("Rust") continua sendo bloqueada.
    descritivo = RascunhoPlano.model_validate_json(json_rascunho_plano().replace('"Power BI"', '"Modelagem com Power BI"'))
    assert verificar_plano(normalizar_habilidades_plano(descritivo, lacunas_ficticias), lacunas_ficticias) == []
    assert descritivo.topicos[0].habilidade == "Power BI"


def test_fidelidade_progresso_e_calculos(lacunas_ficticias):
    ok = RascunhoProgresso.model_validate_json(json_rascunho_progresso())
    assert verificar_progresso(ok) == []

    sem_ev = RascunhoProgresso.model_validate_json(json_rascunho_progresso(evidencias=[]))
    assert any("evidência" in p for p in verificar_progresso(sem_ev))

    sem_acerto = RascunhoProgresso.model_validate_json(json_rascunho_progresso(exercicio_correto=False))
    assert any("nenhum exercício corrigido" in p for p in verificar_progresso(sem_acerto))

    assert calcular_data_conclusao(date(2026, 11, 16), total_horas=12.0, horas_semanais=6.0) == date(2026, 11, 30)
    assert calcular_desempenho([
        Exercicio(enunciado="Q1", tipo="objetiva", resposta_formando="A", correto=True, feedback="Ok"),
        Exercicio(enunciado="Q2", tipo="objetiva", resposta_formando="B", correto=False, feedback="Rever"),
    ]) == 0.5

    agora = datetime(2026, 11, 25, 16, 0, tzinfo=timezone.utc)
    prog = montar_progresso(ok, "f01", agora=agora)
    assert prog.sessoes[0].data == agora
    assert prog.sessoes[0].desempenho == 0.5

    plano = montar_plano(RascunhoPlano.model_validate_json(json_rascunho_plano()), lacunas_ficticias, "f01", date(2026, 11, 25))
    assert plano.data_prevista_conclusao == date(2026, 12, 9)


# ---------------------------------------------- serviço (Time Agno + retries)

def test_fluxo_completo_retry_fidelidade_e_verificador(fabrica, lacunas_ficticias, curriculo_ficticio):
    # 1ª tentativa do extrator_plano erra (inventa habilidade "Rust"); 2ª tentativa corrige!
    servico, modelo = fabrica(
        "Olá! Vamos fazer seu diagnóstico de Power BI.",
        "Perfeito! Com 6h/semana vamos começar por modelagem dimensional.",
        json_rascunho_plano(habilidade_extra="Rust"),
        json_rascunho_plano(),
        json_rascunho_progresso(),
    )

    id_sessao, msg = servico.iniciar(lacunas_ficticias, curriculo_ficticio)
    assert "diagnóstico" in msg
    assert "modelagem" in servico.conversar(id_sessao, "Tenho 6 horas por semana.")

    plano, progresso = servico.resultado(id_sessao)
    assert isinstance(plano, PlanoEstudos)
    assert isinstance(progresso, RegistroProgresso)
    assert modelo.chamadas == 5  # iniciar + conversar + 2 tentativas plano + 1 progresso

    rel = verificar_contrato(
        "tutor_estudos",
        entradas={"Lacunas": lacunas_ficticias, "Curriculo": curriculo_ficticio},
        saidas={"PlanoEstudos": plano, "RegistroProgresso": progresso},
    )
    assert rel.erros == []


def test_erro_fidelidade_apos_esgotar_tentativas(fabrica, lacunas_ficticias, curriculo_ficticio):
    servico, _ = fabrica(
        "Olá!",
        json_rascunho_plano(habilidade_extra="Rust"),
        json_rascunho_plano(habilidade_extra="Rust"),
    )
    id_sessao, _ = servico.iniciar(lacunas_ficticias, curriculo_ficticio)
    with pytest.raises(ErroFidelidade) as exc:
        servico.resultado(id_sessao)
    assert any("Rust" in p for p in exc.value.problemas)


def test_retry_quando_llm_devolve_horas_zero(fabrica, lacunas_ficticias, curriculo_ficticio):
    # Simula erro real do llama3.2 (horas_estimadas=0) corrigido automaticamente na 2ª tentativa
    plano_horas_zero = json_rascunho_plano().replace('"horas_estimadas": 6.0', '"horas_estimadas": 0')
    servico, modelo = fabrica(
        "Olá!",
        plano_horas_zero,
        json_rascunho_plano(),
        json_rascunho_progresso(),
    )
    id_sessao, _ = servico.iniciar(lacunas_ficticias, curriculo_ficticio)
    plano, _ = servico.resultado(id_sessao)
    assert all(t.horas_estimadas > 0 for t in plano.topicos)
    assert modelo.chamadas == 4


def test_avaliar_exercicio_codigo_e_discursiva(fabrica):
    servico, _ = fabrica(json.dumps({"correto": True, "feedback": "Boa definição de tabela fato."}))
    ex_cod = servico.avaliar_exercicio(
        enunciado="Implemente dobro(x)",
        tipo="codigo",
        resposta_formando="def dobro(x):\n    return x * 2",
        testes="assert dobro(3) == 6",
    )
    assert ex_cod.correto is True

    ex_disc = servico.avaliar_exercicio(
        enunciado="O que é tabela fato?",
        tipo="discursiva",
        resposta_formando="Registra eventos numéricos ligados às dimensões.",
    )
    assert ex_disc.correto is True and "tabela fato" in (ex_disc.feedback or "")


# -------------------------------------------------------- servidor e AgentOS

def test_rotas_contrato_e_agentos(fabrica, lacunas_ficticias, curriculo_ficticio):
    servico, _ = fabrica(
        "Olá! Qual sua disponibilidade semanal?",
        "Vamos começar a aula de Power BI.",
        json_rascunho_plano(),
        json_rascunho_progresso(),
        "Sessão 2 iniciada.",
        json_rascunho_plano(habilidade_extra="Rust"),
        json_rascunho_plano(habilidade_extra="Rust"),
    )
    cliente = TestClient(criar_agent_os(servico).get_app())

    assert cliente.get("/config").status_code == 200
    assert any(t["id"] == "tutor-estudos-time" for t in cliente.get("/teams").json())
    agentes = {a["id"] for a in cliente.get("/agents").json()}
    assert {"tutor-planejador", "tutor-professor", "tutor-avaliador"} <= agentes

    corpo = {
        "lacunas": lacunas_ficticias.model_dump(mode="json"),
        "curriculo": curriculo_ficticio.model_dump(mode="json"),
    }
    r_init = cliente.post("/contrato/iniciar", json=corpo)
    assert r_init.status_code == 200
    id_sessao = r_init.json()["id_sessao"]

    r_run = cliente.post(
        "/teams/tutor-estudos-time/runs",
        data={"message": "Tenho 6h por semana.", "session_id": id_sessao, "stream": "false"},
    )
    assert r_run.status_code == 200
    assert r_run.json()["content"] == "Vamos começar a aula de Power BI."

    r_res = cliente.get(f"/contrato/resultado/{id_sessao}")
    assert r_res.status_code == 200
    PlanoEstudos.model_validate(r_res.json()["plano_estudos"])
    RegistroProgresso.model_validate(r_res.json()["registro_progresso"])

    # 404 para sessão inexistente e 422 para erro de fidelidade / schema inválido
    assert cliente.get("/contrato/resultado/nao-existe").status_code == 404
    id_sessao_2 = cliente.post("/contrato/iniciar", json=corpo).json()["id_sessao"]
    r_err = cliente.get(f"/contrato/resultado/{id_sessao_2}")
    assert r_err.status_code == 422 and r_err.json()["detail"]["erro"] == "fidelidade"
    assert cliente.post("/contrato/iniciar", json={"lacunas": {}}).status_code == 422


# ------------------------------------------------------------------------ CLI

def test_cli_conversar_e_retomar_sessao(fabrica, lacunas_ficticias, curriculo_ficticio, tmp_path, monkeypatch):
    servico, _ = fabrica(
        "Olá! Quantas horas você tem?",
        "Beleza, 6h/semana anotado.",
        json_rascunho_plano(),
        json_rascunho_progresso(),
        "Bem-vindo de volta! Paramos em Power BI.",
        json_rascunho_plano(),
        json_rascunho_progresso(),
    )
    monkeypatch.setattr(cli, "criar_servico", lambda: (servico, None))

    arq_lac = tmp_path / "lacunas.json"
    arq_cv = tmp_path / "curriculo.json"
    salvar(lacunas_ficticias, arq_lac)
    salvar(curriculo_ficticio, arq_cv)

    falas = iter(["Tenho 6h por semana.", "/fim"])
    args = cli.argparse.Namespace(entrada=[arq_lac, arq_cv], sessao=None, saida_dir=tmp_path / "saidas")
    assert cli.cmd_conversar(args, entrada=lambda _: next(falas)) == 0
    PlanoEstudos.model_validate(json.loads((tmp_path / "saidas" / "plano_estudos.json").read_text(encoding="utf-8")))
    RegistroProgresso.model_validate(
        json.loads((tmp_path / "saidas" / "registro_progresso.json").read_text(encoding="utf-8"))
    )

    # Retomada de sessão existente (F8) usando o session_id persistido no SQLite
    sessoes_salvas = servico.time.db.get_sessions()
    assert sessoes_salvas
    id_salvo = sessoes_salvas[0].session_id
    falas_retomada = iter(["/fim"])
    args_ret = cli.argparse.Namespace(entrada=None, sessao=id_salvo, saida_dir=tmp_path / "saidas2")
    assert cli.cmd_conversar(args_ret, entrada=lambda _: next(falas_retomada)) == 0
    assert (tmp_path / "saidas2" / "plano_estudos.json").exists()


def test_cli_entrada_incompleta(tmp_path, curriculo_ficticio):
    arq_cv = tmp_path / "curriculo.json"
    salvar(curriculo_ficticio, arq_cv)
    assert cli.main(["conversar", "--entrada", str(arq_cv)]) == 2
