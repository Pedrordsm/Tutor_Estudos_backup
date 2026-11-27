"""Modelos auxiliares das rotas do contrato, avaliação e extração estruturada."""

from typing import Optional

from pydantic import BaseModel, Field

from carreira_contratos import (
    Curriculo,
    Exercicio,
    Habilidade,
    Lacunas,
    PlanoEstudos,
    RegistroProgresso,
)
from carreira_contratos.modelos import Situacao


# ----------------------------------------------------------- modelos das rotas

class EntradaContrato(BaseModel):
    lacunas: Lacunas
    curriculo: Curriculo


class SessaoIniciada(BaseModel):
    id_sessao: str
    mensagem: str


class SaidaContrato(BaseModel):
    plano_estudos: PlanoEstudos
    registro_progresso: RegistroProgresso


# -------------------------------------------------- avaliação de um exercício

class AvaliacaoExercicio(BaseModel):
    correto: bool
    feedback: str = Field(min_length=1)


# -------------------------------------------- rascunhos extraídos pelo modelo
# Campos objetivos (cabecalho, ordem, data_prevista_conclusao, data da sessão
# e desempenho) são sempre calculados/preenchidos em código (calculos.py).

class RascunhoTopico(BaseModel):
    habilidade: str = Field(min_length=1)
    titulo: str = Field(min_length=1)
    objetivos: list[str] = Field(min_length=1, description="Pelo menos 1 objetivo de aprendizagem do tópico.")
    prerequisitos: list[str] = []
    horas_estimadas: float = Field(gt=0)
    atividades: list[str] = []
    projeto_pratico: Optional[str] = None
    situacao: Situacao = "pendente"


class RascunhoPlano(BaseModel):
    horas_semanais: float = Field(gt=0, default=6.0)
    topicos: list[RascunhoTopico] = Field(min_length=1)


class RascunhoSessao(BaseModel):
    topico: str = Field(min_length=1)
    resumo_explicacao: str = ""
    exercicios: list[Exercicio] = []


class RascunhoProgresso(BaseModel):
    sessoes: list[RascunhoSessao] = []
    habilidades_adquiridas: list[Habilidade] = []
    ajustes_plano: list[str] = []
