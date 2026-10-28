"""Regras puras do painel do Beatle Contador.

Tempo, fila, certificado, última busca e retomada. Sem banco e sem interface.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

INTERVALO_FILA_GRANDE_MINUTOS = 10
INTERVALO_FIM_DA_FILA_MINUTOS = 60
HORAS_RETOMADA_AUTOMATICA = 2


def nsu_num(valor) -> int:
    digitos = "".join(c for c in str(valor or "0") if c.isdigit()) or "0"
    try:
        return int(digitos)
    except ValueError:
        return 0


def cnpj_base(cnpj) -> str:
    digitos = "".join(c for c in str(cnpj or "") if c.isdigit())
    return digitos[:8]


def duracao_curta(segundos: int) -> str:
    segundos = max(0, int(segundos))
    if segundos < 60:
        return "menos de 1 min"
    minutos = (segundos + 59) // 60
    if minutos < 60:
        return f"{minutos} min"
    horas = minutos // 60
    resto = minutos % 60
    if resto == 0:
        return f"{horas} h"
    return f"{horas} h {resto} min"


def milhar(valor: int) -> str:
    return f"{int(valor):,}".replace(",", ".")


def estado_da_empresa(
    status,
    bloqueio_656_ate,
    bloqueio_manual_ate,
    ultimo_nsu,
    max_nsu,
    agora: Optional[datetime] = None,
) -> str:
    agora = agora or datetime.now()
    if status != "aprovado":
        return "bloqueada"
    if bloqueio_656_ate and agora < bloqueio_656_ate:
        return "bloqueada"
    if bloqueio_manual_ate and agora < bloqueio_manual_ate:
        return "bloqueada"
    ult = nsu_num(ultimo_nsu)
    mx = nsu_num(max_nsu)
    if mx > 0 and ult >= mx:
        return "fim da fila"
    return "na fila"


def motivo_bloqueio(status, bloqueio_656_ate, bloqueio_manual_ate, agora: datetime) -> Optional[str]:
    if status != "aprovado":
        return "status"
    if bloqueio_656_ate and agora < bloqueio_656_ate:
        return "656"
    if bloqueio_manual_ate and agora < bloqueio_manual_ate:
        return "manual"
    return None


def _segundos_ate(alvo: Optional[datetime], agora: datetime) -> int:
    if not alvo:
        return 0
    return max(0, int((alvo - agora).total_seconds()))


def agenda_consulta(
    *,
    status,
    bloqueio_656_ate,
    bloqueio_manual_ate,
    ultimo_nsu,
    max_nsu,
    ultima_consulta,
    proxima_consulta_periodica,
    modulo_nfse_ativo: bool,
    proxima_consulta_nfse,
    agora: Optional[datetime] = None,
) -> dict:
    """Diz se a Receita pode ser consultada agora, e quanto falta."""
    agora = agora or datetime.now()
    modulo_nfse_ativo = False
    estado = estado_da_empresa(
        status, bloqueio_656_ate, bloqueio_manual_ate, ultimo_nsu, max_nsu, agora
    )
    bloqueio = motivo_bloqueio(status, bloqueio_656_ate, bloqueio_manual_ate, agora)

    nfe_ok = False
    nfe_seg = 0
    nfe_motivo = "agora"
    if bloqueio in ("status", "manual"):
        alvo = bloqueio_manual_ate if bloqueio == "manual" else None
        nfe_seg = _segundos_ate(alvo, agora)
        nfe_motivo = "bloqueio"
    elif bloqueio == "656":
        nfe_seg = _segundos_ate(bloqueio_656_ate, agora)
        nfe_motivo = "656"
    elif estado == "fim da fila":
        alvo = proxima_consulta_periodica
        if alvo is None and ultima_consulta:
            alvo = ultima_consulta + timedelta(minutes=INTERVALO_FIM_DA_FILA_MINUTOS)
        if alvo and agora < alvo:
            nfe_seg = _segundos_ate(alvo, agora)
            nfe_motivo = "fim"
        else:
            nfe_ok = True
    else:
        if ultima_consulta:
            alvo = ultima_consulta + timedelta(minutes=INTERVALO_FILA_GRANDE_MINUTOS)
            if agora < alvo:
                nfe_seg = _segundos_ate(alvo, agora)
                nfe_motivo = "fila"
            else:
                nfe_ok = True
        else:
            nfe_ok = True

    nfse_ok = False
    nfse_seg = None
    if modulo_nfse_ativo and bloqueio not in ("status", "manual"):
        if proxima_consulta_nfse and agora < proxima_consulta_nfse:
            nfse_seg = _segundos_ate(proxima_consulta_nfse, agora)
        else:
            nfse_ok = True

    if nfe_ok or nfse_ok:
        return {
            "estado": estado,
            "pode_consultar_nfe": nfe_ok,
            "pode_consultar_nfse": nfse_ok,
            "pode_consultar": True,
            "segundos_ate_proxima": 0,
            "motivo_tempo": "agora",
        }

    esperas = []
    if not nfe_ok:
        esperas.append((nfe_seg, nfe_motivo))
    if nfse_seg is not None:
        esperas.append((nfse_seg, "nfse"))
    if not esperas:
        return {
            "estado": estado,
            "pode_consultar_nfe": False,
            "pode_consultar_nfse": False,
            "pode_consultar": False,
            "segundos_ate_proxima": 0,
            "motivo_tempo": "bloqueio",
        }
    segundos, motivo = min(esperas, key=lambda item: item[0])
    if nfe_motivo == "656" and segundos == nfe_seg:
        motivo = "656"
    return {
        "estado": estado,
        "pode_consultar_nfe": False,
        "pode_consultar_nfse": False,
        "pode_consultar": False,
        "segundos_ate_proxima": segundos,
        "motivo_tempo": motivo,
    }


def fila_nsu(ultimo, maximo) -> Optional[int]:
    if nsu_num(maximo) <= 0:
        return None
    return max(0, nsu_num(maximo) - nsu_num(ultimo))


def rotulo_estado(estado: str) -> str:
    return {
        "na fila": "Na fila",
        "fim da fila": "Fim da fila",
        "bloqueada": "Bloqueada",
    }.get(estado or "", "Na fila")


def rotulo_tempo(segundos: int, motivo: str) -> str:
    if motivo == "agora" or int(segundos or 0) <= 0:
        return "agora"
    texto = duracao_curta(segundos)
    if motivo == "656":
        return f"656 libera em {texto}"
    return f"em {texto}"


def rotulo_fila(fila_nfe: Optional[int], fila_nfse: Optional[int], modulo_nfse: bool, estado: str) -> str:
    if fila_nfe is None:
        return "—"
    if fila_nfe <= 0 and estado == "fim da fila":
        return "vazia"
    return milhar(fila_nfe)


def rotulo_ultima(qtd: Optional[int], cstat: Optional[str]) -> str:
    codigo = (cstat or "").strip()
    if qtd is None and not codigo:
        return "—"
    if codigo == "137":
        return "sem novidade · 137"
    if codigo == "656":
        return "0 · 656"
    if qtd is None:
        return codigo
    if codigo:
        return f"{int(qtd)} notas · {codigo}"
    return f"{int(qtd)} notas"


def rotulo_certificado(
    bloqueio_656_ate,
    tem_certificado: bool,
    validade,
    agora: Optional[datetime] = None,
) -> str:
    agora = agora or datetime.now()
    if bloqueio_656_ate and agora < bloqueio_656_ate:
        return f"656 até {bloqueio_656_ate.strftime('%H:%M')}"
    if not tem_certificado:
        return "sem certificado"
    if validade is None:
        return "OK"
    dias = (validade - agora).days
    if dias < 0:
        return "vencido"
    if dias == 0:
        return "vence hoje"
    if dias <= 30:
        return f"vence em {dias} dias"
    return "OK"


def rotulo_notas(qtd_nfe: Optional[int], qtd_nfse: Optional[int], modulo_nfse: bool) -> str:
    if qtd_nfe is None:
        return "—"
    return f"NF-e {int(qtd_nfe)}"


def momento_retomada(agora: datetime, automatico: bool, horas: int = HORAS_RETOMADA_AUTOMATICA):
    if not automatico:
        return None
    return agora + timedelta(hours=horas)


def deve_retomar(retomar_em, agora: datetime) -> bool:
    return bool(retomar_em and agora >= retomar_em)


def _n_empresas(n: int) -> str:
    return "1 empresa" if n == 1 else f"{n} empresas"


def faixa_atencao(
    *,
    agente_ativo: bool,
    agente_retomar_em,
    empresas: list,
    agora: Optional[datetime] = None,
) -> str:
    """Texto da faixa. Vazio quando todas as buscas estão ligadas."""
    agora = agora or datetime.now()
    total = len(empresas or [])
    if not agente_ativo:
        if agente_retomar_em and agora < agente_retomar_em:
            hora = agente_retomar_em.strftime("%H:%M")
            if total:
                return (
                    f"Buscas paradas para {_n_empresas(total)}. "
                    f"Retorno automático às {hora}."
                )
            return f"Buscas paradas. Retorno automático às {hora}."
        return "Buscas paradas. Elas ficam desligadas até você retomar."

    pausadas = [e for e in (empresas or []) if not e.get("sync_ativo", True)]
    if not pausadas:
        return ""
    n = len(pausadas)
    com_hora = []
    sem_hora = 0
    for empresa in pausadas:
        quando = empresa.get("retomar_em")
        if quando and agora < quando:
            com_hora.append(quando)
        else:
            sem_hora += 1
    if com_hora and sem_hora == 0:
        hora = min(com_hora).strftime("%H:%M")
        return f"{_n_empresas(n)} com busca desligada. Retorno automático às {hora}."
    if com_hora and sem_hora:
        hora = min(com_hora).strftime("%H:%M")
        return (
            f"{_n_empresas(n)} com busca desligada. "
            f"Parte volta sozinha às {hora}; as demais ficam pausadas até você retomar."
        )
    return f"{_n_empresas(n)} com busca desligada. Elas ficam pausadas até você retomar."


def escolher_empresa(empresas: list, ultimo_id: Optional[int] = None) -> Optional[dict]:
    """Uma empresa por ciclo. Cada CNPJ base entra uma vez."""
    por_base = {}
    for empresa in empresas or []:
        if not empresa.get("sync_ativo", True):
            continue
        if not empresa.get("pode_consultar"):
            continue
        base = empresa.get("cnpj_base") or str(empresa.get("id") or "")
        atual = por_base.get(base)
        eid = int(empresa.get("id") or 0)
        if atual is None or eid < int(atual.get("id") or 0):
            por_base[base] = empresa
    candidatos = sorted(por_base.values(), key=lambda item: int(item.get("id") or 0))
    if not candidatos:
        return None
    if ultimo_id is None:
        return candidatos[0]
    for empresa in candidatos:
        if int(empresa.get("id") or 0) > int(ultimo_id):
            return empresa
    return candidatos[0]


def montar_painel(
    *,
    agora: datetime,
    status,
    bloqueio_656_ate,
    bloqueio_manual_ate,
    ultimo_nsu,
    max_nsu,
    ultima_consulta,
    proxima_consulta_periodica,
    modulo_nfse_ativo: bool,
    ultimo_nsu_nfse,
    max_nsu_nfse,
    proxima_consulta_nfse,
    ultima_nfe_qtd,
    ultima_nfse_qtd,
    ultimo_cstat,
    tem_certificado: bool,
    certificado_valido_ate,
) -> dict:
    agenda = agenda_consulta(
        status=status,
        bloqueio_656_ate=bloqueio_656_ate,
        bloqueio_manual_ate=bloqueio_manual_ate,
        ultimo_nsu=ultimo_nsu,
        max_nsu=max_nsu,
        ultima_consulta=ultima_consulta,
        proxima_consulta_periodica=proxima_consulta_periodica,
        modulo_nfse_ativo=modulo_nfse_ativo,
        proxima_consulta_nfse=proxima_consulta_nfse,
        agora=agora,
    )
    estado = agenda["estado"]
    fila_nfe = fila_nsu(ultimo_nsu, max_nsu)
    fila_nfse = fila_nsu(ultimo_nsu_nfse, max_nsu_nfse) if modulo_nfse_ativo else None
    return {
        "estado": estado,
        "estado_rotulo": rotulo_estado(estado),
        "pode_consultar": agenda["pode_consultar"],
        "pode_consultar_nfe": agenda["pode_consultar_nfe"],
        "pode_consultar_nfse": agenda["pode_consultar_nfse"],
        "segundos_ate_proxima": agenda["segundos_ate_proxima"],
        "tempo_rotulo": rotulo_tempo(agenda["segundos_ate_proxima"], agenda["motivo_tempo"]),
        "fila_nfe": fila_nfe,
        "fila_nfse": fila_nfse,
        "fila_rotulo": rotulo_fila(fila_nfe, fila_nfse, modulo_nfse_ativo, estado),
        "ultima_nfe_qtd": ultima_nfe_qtd,
        "ultima_nfse_qtd": ultima_nfse_qtd,
        "ultimo_cstat": ultimo_cstat,
        "ultima_rotulo": rotulo_ultima(ultima_nfe_qtd, ultimo_cstat),
        "certificado_rotulo": rotulo_certificado(
            bloqueio_656_ate, tem_certificado, certificado_valido_ate, agora
        ),
        "notas_rotulo": rotulo_notas(ultima_nfe_qtd, ultima_nfse_qtd, modulo_nfse_ativo),
    }
