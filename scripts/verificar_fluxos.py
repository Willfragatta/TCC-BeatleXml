"""Percorre os fluxos da demonstração contra a aplicação já no ar."""
import io
import sys
import zipfile
from pathlib import Path

import requests

BASE = "http://localhost:8000"
RAIZ = Path(__file__).resolve().parents[1]
CSRF = {"X-Requested-With": "BeatleXML"}


def login(email, senha):
    sessao = requests.Session()
    resp = sessao.post(
        f"{BASE}/auth/login",
        data={"email": email, "senha": senha},
        allow_redirects=False,
        timeout=30,
    )
    if resp.status_code not in (302, 303):
        raise SystemExit(f"login {email} devolveu {resp.status_code}: {resp.text[:300]}")
    if "access_token" not in sessao.cookies:
        raise SystemExit(f"login {email} não gravou o cookie. Destino: {resp.headers.get('location')}")
    return sessao


def empresas(sessao):
    resp = sessao.get(f"{BASE}/api/empresas/", timeout=30)
    resp.raise_for_status()
    return resp.json()


def main():
    gestor = login("gestor@demo.local", "Gestor#2026")
    operador = login("operador@demo.local", "Operador#2026")
    leitura = login("leitura@demo.local", "Leitura#2026")
    contador = login("contador@demo.local", "Contador#2026")
    suporte = login("suporte@demo.local", "Suporte#2026")

    nomes_gestor = {e["razao_social"] for e in empresas(gestor)}
    nomes_contador = {e["razao_social"] for e in empresas(contador)}
    nomes_suporte = {e["razao_social"] for e in empresas(suporte)}
    assert "Padaria Isolada Ltda" not in nomes_gestor
    assert "Padaria Isolada Ltda" not in nomes_contador
    assert "Padaria Isolada Ltda" in nomes_suporte
    assert "Mercado Bom Preço Matriz Ltda" in nomes_contador
    print("isolamento ok", sorted(nomes_contador))

    notas = gestor.get(f"{BASE}/api/notas/", params={"limit": 20}, timeout=30)
    notas.raise_for_status()
    lista = notas.json()
    itens = lista if isinstance(lista, list) else lista.get("notas") or lista.get("items") or []
    if not itens:
        raise SystemExit(f"nenhuma nota na listagem: {str(lista)[:400]}")
    nota_id = itens[0]["id"]
    print("nota", nota_id, itens[0].get("emitente_nome"))

    fin = gestor.get(f"{BASE}/api/financeiro/parcelas", params={"status": "pendente"}, timeout=30)
    fin.raise_for_status()
    parcelas = fin.json()
    linhas = parcelas if isinstance(parcelas, list) else parcelas.get("parcelas") or parcelas.get("items") or []
    if len(linhas) < 1:
        raise SystemExit(f"financeiro vazio: {str(parcelas)[:400]}")
    print("parcelas", len(linhas), linhas[0].get("forma_pagamento_label"), linhas[0].get("valor"))

    bloqueio = leitura.post(
        f"{BASE}/api/notas/download-lote",
        json={"ids": [nota_id]},
        headers=CSRF,
        timeout=30,
    )
    if bloqueio.status_code != 403:
        raise SystemExit(f"leitura deveria receber 403 no ZIP, veio {bloqueio.status_code}")
    print("leitura bloqueada no ZIP")

    pacote = contador.post(
        f"{BASE}/api/notas/download-lote",
        json={"ids": [nota_id]},
        headers=CSRF,
        timeout=60,
    )
    pacote.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(pacote.content)) as arquivo:
        nomes = arquivo.namelist()
    if not any(nome.endswith(".xml") for nome in nomes):
        raise SystemExit(f"ZIP sem XML: {nomes}")
    print("zip", nomes)

    pdf = gestor.get(f"{BASE}/api/notas/danfe/{nota_id}", timeout=60)
    pdf.raise_for_status()
    if not pdf.content.startswith(b"%PDF"):
        raise SystemExit(f"DANFE não é PDF: {pdf.content[:80]!r}")
    print("danfe", len(pdf.content), "bytes")

    token = requests.post(
        f"{BASE}/api/uploader/login",
        json={"email": "gestor@demo.local", "senha": "Gestor#2026"},
        timeout=30,
    )
    token.raise_for_status()
    corpo = token.json()
    empresa_id = next(e["id"] for e in corpo["empresas"] if e["cnpj"] == "11222333000181")
    xml = (RAIZ / "scripts" / "fixtures" / "nfce-caixa.xml").read_bytes()
    envio = requests.post(
        f"{BASE}/api/uploader/upload",
        headers={"Authorization": f"Bearer {corpo['token']}"},
        data={"empresa_id": str(empresa_id)},
        files={"arquivos": ("nfce-caixa.xml", xml, "application/xml")},
        timeout=30,
    )
    envio.raise_for_status()
    print("uploader", envio.json())

    contador_token = requests.post(
        f"{BASE}/api/contador/login",
        json={"email": "contador@demo.local", "senha": "Contador#2026"},
        timeout=30,
    )
    contador_token.raise_for_status()
    ctok = contador_token.json()["token"]
    painel = requests.get(
        f"{BASE}/api/contador/empresas",
        headers={"Authorization": f"Bearer {ctok}"},
        timeout=30,
    )
    painel.raise_for_status()
    dados = painel.json()
    empresas_agente = dados if isinstance(dados, list) else (dados.get("empresas") or [])
    razoes = [e.get("razao_social") for e in empresas_agente]
    if any("Padaria" in (r or "") for r in razoes):
        raise SystemExit(f"agente do contador viu empresa isolada: {razoes}")
    print("contador agente", razoes)
    pedido = requests.post(
        f"{BASE}/api/contador/sincronizar/{empresas_agente[0]['id']}",
        headers={"Authorization": f"Bearer {ctok}"},
        timeout=30,
    )
    pedido.raise_for_status()
    corpo_pedido = pedido.json()
    if corpo_pedido.get("executado"):
        raise SystemExit(f"sincronização disparou consulta sem certificado: {corpo_pedido}")
    print("pedido de sync sem certificado", corpo_pedido.get("motivo") or corpo_pedido.get("estado"))

    ligar = gestor.post(
        f"{BASE}/config/dois-fatores",
        json={"ligado": True, "senha_atual": "Gestor#2026"},
        headers=CSRF,
        timeout=30,
    )
    ligar.raise_for_status()
    segundo = requests.post(
        f"{BASE}/auth/login",
        data={"email": "gestor@demo.local", "senha": "Gestor#2026"},
        allow_redirects=False,
        timeout=30,
    )
    destino = segundo.headers.get("location") or ""
    if "verificar-2fa" not in destino:
        raise SystemExit(f"2FA não redirecionou: {segundo.status_code} {destino}")
    print("2fa destino", destino)
    try:
        caixa = requests.get("http://localhost:8025/api/v1/messages", timeout=5)
        caixa.raise_for_status()
        mensagens = caixa.json()
        total = mensagens.get("total") or mensagens.get("messages_count") or len(mensagens.get("messages") or [])
        print("mailpit", total)
    except Exception as exc:
        print("mailpit indisponível:", exc)

    desligar = gestor.post(
        f"{BASE}/config/dois-fatores",
        json={"ligado": False, "senha_atual": "Gestor#2026"},
        headers=CSRF,
        timeout=30,
    )
    desligar.raise_for_status()
    print("2fa desligado de novo")
    print("fluxos ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
