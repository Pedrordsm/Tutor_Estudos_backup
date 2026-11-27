# Módulo 4 — Tutor de estudos

Módulo conversacional do ecossistema de agentes **"Do diploma ao primeiro emprego"** (Inteligência Artificial — IFES Campus Serra · BSI).

**Grupo 4:** Samiya, Tertuliano, Pedro Renã, Kaio e Heitor.

Recebe `Lacunas` + `Curriculo`, conduz a tutoria com um **Time Agno** (`Planejador`, `Professor` e `Avaliador`), executa exercícios de código em sandbox isolada e produz `PlanoEstudos` + `RegistroProgresso` no contrato `tutor_estudos`.

## Arquitetura e arquivos

| Arquivo | Responsabilidade |
|---|---|
| `src/tutor_estudos/config.py` | Lê variáveis de ambiente e `.env` (`MODELO_LLM`, `BANCO_SQLITE`, `PORTA`, `TIMEOUT_SANDBOX`, `AGNO_TELEMETRY`). |
| `src/tutor_estudos/modelos.py` | Schemas Pydantic das rotas `/contrato/*`, avaliação de exercícios e rascunhos de extração. |
| `src/tutor_estudos/sandbox.py` | Execução isolada de Python (incluindo `assert` e funções `def test_*()` estilo pytest, bloqueio AST de rede/sistema e timeout) e SQL somente leitura em SQLite em memória. |
| `src/tutor_estudos/fidelidade.py` | Verificação determinística de fidelidade e guardrails (habilidades dentro das lacunas, cobertura de prioridade alta, pré-requisitos sem ciclo, evidência obrigatória em habilidades adquiridas). |
| `src/tutor_estudos/calculos.py` | Cálculos objetivos em código (data prevista de conclusão, data real da sessão, numeração de tópicos e desempenho 0..1). |
| `src/tutor_estudos/servico.py` | Time Agno (`Planejador`, `Professor`, `Avaliador`) com memória em SQLite, retry HTTP 429, extratores com loop de correção (`fidelidade.py`) e avaliação unitária de exercícios. |
| `src/tutor_estudos/servidor.py` | Servidor AgentOS expondo o time, os agentes e as rotas `POST /contrato/iniciar` e `GET /contrato/resultado/{id_sessao}`. |
| `src/tutor_estudos/cli.py` | Comandos `conversar` (nova sessão ou retomada com `--sessao`) e `servir`. |
| `tests/` | Testes automatizados offline usando `ModeloRoteirizado` (sem acesso à rede). |

## Funcionalidades cobertas (Caderno M4)

- **F1:** Diagnóstico inicial a partir de `Lacunas` + `Curriculo` com perguntas rápidas.
- **F2:** `PlanoEstudos` priorizado com pré-requisitos e datas calculadas em código.
- **F3:** Aulas didáticas em sessões pelo `Agente Professor`.
- **F4:** Exercícios objetivos, discursivos e de código (sandbox isolada Python/pytest + SQL em memória).
- **F5:** Adaptação do plano ao desempenho e registro em `ajustes_plano`.
- **F6:** Sugestão de projetos práticos (`projeto_pratico`).
- **F7:** `RegistroProgresso` e `habilidades_adquiridas` somente com evidência comprovada.
- **F8:** Memória persistente em SQLite entre sessões (`--sessao` na CLI ou interface do AgentOS).

## Instalação

Requer Python 3.10 ou superior:

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e caminho/do/kit/contratos
pip install -e ".[ollama,anthropic,groq,testes]"
```

## Configuração

Copie `.env.exemplo` para `.env` e escolha o provedor desejado em `MODELO_LLM`:

- **Ollama (local / gratuito):** `MODELO_LLM=ollama:qwen2.5:7b` ou `ollama:llama3.2` (com o Ollama em execução).
- **Groq (camada gratuita):** `MODELO_LLM=groq:openai/gpt-oss-120b` (ou `groq:qwen/qwen3.8-27b`) e `GROQ_API_KEY=...`.
- **Anthropic (Claude):** `MODELO_LLM=anthropic:claude-sonnet-4-5` e `ANTHROPIC_API_KEY=...`.

> Nunca envie o `.env` para o repositório.

## Execução

> Todos os comandos abaixo podem ser executados tanto com `python -m tutor_estudos ...` (recomendado no Windows com Device Guard) quanto com o atalho `tutor-estudos ...`.

### Conversar no terminal e gerar saídas

```bash
python -m tutor_estudos conversar \
  --entrada caminho/do/kit/exemplos/f01/lacunas.json caminho/do/kit/exemplos/f01/curriculo.json \
  --saida-dir saidas/f01
```

Digite `/fim` para gravar `plano_estudos.json` e `registro_progresso.json`. Para continuar a mesma sessão depois (F8):

```bash
python -m tutor_estudos conversar --sessao <ID_DA_SESSAO> --saida-dir saidas/f01
```

### Subir o servidor AgentOS

```bash
python -m tutor_estudos servir
```

- Documentação interativa das rotas do contrato (`POST /contrato/iniciar` e `GET /contrato/resultado/{id_sessao}`): `http://localhost:7777/docs`.
- Interface gráfica: conecte `http://localhost:7777` em `https://os.agno.com` e selecione o time **Tutor de estudos**.

### Verificar conformidade com o contrato

```bash
python -m carreira_contratos.verificar tutor_estudos \
  --entrada caminho/do/kit/exemplos/f01/lacunas.json caminho/do/kit/exemplos/f01/curriculo.json \
  --saida saidas/f01/plano_estudos.json saidas/f01/registro_progresso.json
```

## Testes automatizados

```bash
python -m pytest -v
```

## Limitações conhecidas

- Exercícios de linguagens sem runtime local na sandbox (como medidas DAX do Power BI) são corrigidos conceitualmente pelo agente avaliador, sem execução.
- Modelos locais muito pequenos (como `llama3.2` 3B) podem apresentar instabilidade em *tool calling* dentro do `Team` (ex.: alucinação de chamada de ferramenta e repetição em loop no histórico) e exigir novas tentativas na extração estruturada; recomenda-se usar modelos com suporte mais robusto a ferramentas (como Claude, Groq 70B ou `qwen2.5:7b`). As falhas observadas estão registradas em `relatorio/anotacoes_falhas.md`.
