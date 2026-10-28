"""Sincronizador do contador: dispara a fila no servidor e mostra o estado."""
import os
import sys
import threading
import time

if getattr(sys, "frozen", False):
    pass
elif __file__:
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if raiz not in sys.path:
        sys.path.insert(0, raiz)

from beatle_contador.api import (
    empresa_sync,
    heartbeat,
    listar_empresas,
    login,
    sincronizar,
    sync_iniciar,
    sync_parar,
)
from beatle_contador.config import (
    DEFAULT_BASE_URL,
    VERSAO,
    clear_session,
    load_config,
    save_config,
)
from beatle_contador.instancia import InstanciaUnica
from app.services.contador_painel import escolher_empresa

# Loop de polling no cliente (servidor decide se consulta a Receita).
rodando = False
# Buscas iniciadas no servidor (contador_agente_ativo).
buscas_ativas = False
_empresas_cache = []
_ultimo_sync_id = None
_sincronizando_id = None
_lock = threading.Lock()
_instancia = InstanciaUnica()
_tray = None


def estado_na_tela(empresa: dict, sincronizando_id) -> str:
    if not empresa.get("sync_ativo", True):
        return "Pausada"
    if not buscas_ativas and not empresa.get("agente_ativo"):
        return "Parada"
    if sincronizando_id and empresa.get("id") == sincronizando_id:
        return "Sincronizando"
    return empresa.get("estado_rotulo") or "Na fila"


def tag_linha(empresa: dict, estado: str) -> str:
    certificado = empresa.get("certificado_rotulo") or ""
    if estado == "Bloqueada" or certificado.startswith("656") or certificado == "vencido":
        return "alerta"
    if certificado == "sem certificado" or certificado.startswith("vence"):
        return "alerta"
    if estado in ("Pausada", "Parada"):
        return "pausa"
    return ""


def titulo_bandeja(empresas: list) -> str:
    if not buscas_ativas:
        return "Beatle Contador — buscas paradas"
    desligadas = sum(1 for empresa in empresas if not empresa.get("sync_ativo", True))
    if desligadas == 1:
        return "Beatle Contador — 1 empresa desligada"
    if desligadas:
        return f"Beatle Contador — {desligadas} empresas desligadas"
    return "Beatle Contador — buscas ligadas"


def _ciclo(cfg, atualizar):
    global buscas_ativas, _empresas_cache, _ultimo_sync_id, _sincronizando_id
    token = cfg.get("token")
    base = cfg.get("base_url") or DEFAULT_BASE_URL
    if not token:
        atualizar([])
        return
    empresas = listar_empresas(base, token)
    if empresas:
        buscas_ativas = bool(empresas[0].get("agente_ativo"))
    with _lock:
        _empresas_cache = list(empresas)
    _sincronizando_id = None
    atualizar(empresas)

    for empresa_hb in empresas:
        eid_hb = empresa_hb.get("id")
        if eid_hb is None:
            continue
        try:
            heartbeat(base, token, int(eid_hb))
        except Exception:
            pass

    if not buscas_ativas:
        return

    empresa = escolher_empresa(empresas, _ultimo_sync_id)
    if not empresa or empresa.get("id") is None:
        return
    eid = int(empresa["id"])
    _ultimo_sync_id = eid
    _sincronizando_id = eid
    empresa["estado_rotulo"] = "Sincronizando"
    atualizar(empresas)
    try:
        sincronizar(base, token, eid)
    except Exception as exc:
        empresa["estado_rotulo"] = f"erro: {exc}"[:40]
        atualizar(empresas)
    _sincronizando_id = None
    try:
        empresas = listar_empresas(base, token)
        if empresas:
            buscas_ativas = bool(empresas[0].get("agente_ativo"))
        with _lock:
            _empresas_cache = list(empresas)
        atualizar(empresas)
    except Exception:
        pass


def loop(atualizar):
    global rodando
    while rodando:
        cfg = load_config()
        try:
            _ciclo(cfg, atualizar)
        except Exception as exc:
            atualizar([{"razao_social": "Falha de conexão", "cnpj": "", "estado_rotulo": str(exc)}])
        for _ in range(60):
            if not rodando:
                break
            time.sleep(1)


def main():
    global rodando, buscas_ativas, _tray

    pedido_mostrar = {"fn": None, "pedido": False}

    def ao_pedir_janela():
        fn = pedido_mostrar["fn"]
        if fn:
            fn()
        else:
            pedido_mostrar["pedido"] = True

    if not _instancia.adquirir(ao_pedir_janela):
        _avisar_ja_aberto()
        return

    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title(f"Beatle Contador {VERSAO}")
    root.geometry("1280x680")
    root.minsize(1100, 520)
    estilo = ttk.Style(root)
    try:
        estilo.theme_use("clam")
    except tk.TclError:
        pass
    estilo.map(
        "Treeview",
        background=[("selected", "#1e3a8a")],
        foreground=[("selected", "#ffffff")],
    )

    cfg0 = load_config()
    email = tk.StringVar(value=cfg0.get("email") or "")
    senha = tk.StringVar()
    base = tk.StringVar(value=cfg0.get("base_url") or DEFAULT_BASE_URL)
    status = tk.StringVar(value="Informe o login do escritório e clique em Entrar.")
    sessao_txt = tk.StringVar(value="")
    botao_buscas = tk.StringVar(value="Iniciar buscas")
    botao_empresa = tk.StringVar(value="Pausar empresa selecionada")

    login_frm = ttk.Frame(root, padding=12)
    login_frm.pack(fill="x")
    ttk.Label(login_frm, text="Servidor").grid(row=0, column=0, sticky="w")
    ttk.Entry(login_frm, textvariable=base, width=52).grid(row=0, column=1, sticky="ew")
    ttk.Label(login_frm, text="E-mail").grid(row=1, column=0, sticky="w", pady=(6, 0))
    ttk.Entry(login_frm, textvariable=email, width=52).grid(row=1, column=1, sticky="ew", pady=(6, 0))
    ttk.Label(login_frm, text="Senha").grid(row=2, column=0, sticky="w", pady=(6, 0))
    ttk.Entry(login_frm, textvariable=senha, show="*", width=52).grid(
        row=2, column=1, sticky="ew", pady=(6, 0)
    )
    ttk.Button(login_frm, text="Entrar", command=lambda: entrar()).grid(
        row=3, column=1, sticky="e", pady=(8, 0)
    )
    login_frm.columnconfigure(1, weight=1)

    sessao = ttk.Frame(root, padding=(12, 10))
    ttk.Label(sessao, textvariable=sessao_txt).pack(side="left", fill="x", expand=True)
    ttk.Button(sessao, textvariable=botao_buscas, command=lambda: alternar_buscas()).pack(
        side="left", padx=8
    )
    ttk.Button(sessao, text="Sair", command=lambda: sair()).pack(side="left")

    banner = tk.Label(
        root,
        text="",
        anchor="w",
        justify="left",
        bg="#fff7ed",
        fg="#9a3412",
        padx=12,
        pady=8,
        font=("TkDefaultFont", 10, "bold"),
    )

    tabela = ttk.Frame(root, padding=(12, 0))
    colunas = ("cnpj", "estado", "tempo", "fila", "ultima", "certificado", "notas", "acao")
    tree = ttk.Treeview(tabela, columns=colunas, show="tree headings", height=16)
    tree.heading("#0", text="Empresa")
    tree.heading("cnpj", text="CNPJ")
    tree.heading("estado", text="Estado")
    tree.heading("tempo", text="Tempo")
    tree.heading("fila", text="Fila")
    tree.heading("ultima", text="Última busca")
    tree.heading("certificado", text="Certificado")
    tree.heading("notas", text="Notas")
    tree.heading("acao", text="Ação")
    tree.column("#0", width=150, minwidth=120, stretch=True)
    tree.column("cnpj", width=118, minwidth=108, stretch=False)
    tree.column("estado", width=96, minwidth=88, stretch=False)
    tree.column("tempo", width=188, minwidth=160, stretch=False)
    tree.column("fila", width=148, minwidth=90, stretch=False)
    tree.column("ultima", width=128, minwidth=110, stretch=False)
    tree.column("certificado", width=146, minwidth=120, stretch=False)
    tree.column("notas", width=148, minwidth=100, stretch=False)
    tree.column("acao", width=76, minwidth=68, stretch=False, anchor="center")
    tree.tag_configure("alerta", background="#fff4e5")
    tree.tag_configure("pausa", background="#f3f4f6")
    sy = ttk.Scrollbar(tabela, orient="vertical", command=tree.yview)
    sx = ttk.Scrollbar(tabela, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
    tree.grid(row=0, column=0, sticky="nsew")
    sy.grid(row=0, column=1, sticky="ns")
    sx.grid(row=1, column=0, sticky="ew")
    tabela.rowconfigure(0, weight=1)
    tabela.columnconfigure(0, weight=1)
    tabela.pack(fill="both", expand=True, pady=8)

    detalhe = tk.StringVar(
        value="Selecione uma empresa para ver o resultado da última NF-e."
    )
    ttk.Label(root, textvariable=detalhe, wraplength=1100, justify="left").pack(
        fill="x", padx=12, pady=(0, 6)
    )

    acoes = ttk.Frame(root, padding=(12, 0))
    acoes.pack(fill="x")
    ttk.Button(acoes, textvariable=botao_empresa, command=lambda: toggle_empresa()).pack(side="left")
    ttk.Label(root, textvariable=status).pack(anchor="w", padx=12, pady=(6, 10))

    iid_para_id = {}
    empresas_por_id = {}

    def mostrar_janela():
        root.after(0, _mostrar)

    def _mostrar():
        root.deiconify()
        root.lift()
        root.attributes("-topmost", True)
        root.after(250, lambda: root.attributes("-topmost", False))
        root.focus_force()

    pedido_mostrar["fn"] = mostrar_janela
    if pedido_mostrar["pedido"]:
        mostrar_janela()

    def modo_sessao(ativo: bool):
        if ativo:
            login_frm.pack_forget()
            sessao.pack(fill="x", before=tabela)
            escritorio = email.get().strip() or "sessão salva"
            sessao_txt.set(f"{escritorio}  ·  {base.get().strip() or DEFAULT_BASE_URL}")
        else:
            sessao.pack_forget()
            banner.pack_forget()
            if not login_frm.winfo_ismapped():
                login_frm.pack(fill="x", before=tabela)

    def atualizar_bandeja(empresas):
        if _tray is None:
            return
        _tray.title = titulo_bandeja(empresas)
        try:
            _tray.update()
        except Exception:
            pass

    def texto_status(empresas):
        if empresas and empresas[0].get("retomada_automatica"):
            return "Retomada automática. As buscas voltaram depois de 2 horas."
        if buscas_ativas:
            return "Buscas ligadas. A próxima consulta respeita o intervalo da Receita."
        return "Buscas paradas. A faixa de atenção lembra de retomar."

    def atualizar(empresas):
        global _empresas_cache
        with _lock:
            _empresas_cache = list(empresas)

        def _():
            nonlocal iid_para_id, empresas_por_id
            selecionado = None
            sel = tree.selection()
            if sel:
                selecionado = iid_para_id.get(sel[0])
            tree.delete(*tree.get_children())
            iid_para_id = {}
            empresas_por_id = {}
            for empresa in empresas:
                eid = empresa.get("id")
                estado = estado_na_tela(empresa, _sincronizando_id)
                tag = tag_linha(empresa, estado)
                iid = tree.insert(
                    "",
                    "end",
                    text=empresa.get("razao_social") or "",
                    values=(
                        empresa.get("cnpj") or "",
                        estado,
                        empresa.get("tempo_rotulo") or "—",
                        empresa.get("fila_rotulo") or "—",
                        empresa.get("ultima_rotulo") or "—",
                        empresa.get("certificado_rotulo") or "—",
                        empresa.get("notas_rotulo") or "—",
                        "Retomar" if not empresa.get("sync_ativo", True) else "Pausar",
                    ),
                    tags=(tag,) if tag else (),
                )
                if eid is not None:
                    iid_para_id[iid] = int(eid)
                    empresas_por_id[int(eid)] = empresa
                    if selecionado == int(eid):
                        tree.selection_set(iid)
                        tree.focus(iid)
            if not tree.selection():
                filhos = tree.get_children()
                if filhos:
                    tree.selection_set(filhos[0])
                    tree.focus(filhos[0])
            faixa = ""
            if empresas:
                faixa = empresas[0].get("faixa_atencao") or ""
            if faixa:
                banner.configure(text=faixa)
                if not banner.winfo_ismapped():
                    banner.pack(fill="x", before=tabela)
            else:
                banner.pack_forget()
            botao_buscas.set("Parar buscas" if buscas_ativas else "Iniciar buscas")
            status.set(texto_status(empresas))
            atualizar_bandeja(empresas)
            atual = tree.selection()
            if atual:
                mostrar_detalhe(atual[0])
            else:
                detalhe.set("Selecione uma empresa para ver o resultado da última NF-e.")
                botao_empresa.set("Pausar empresa selecionada")

        root.after(0, _)

    def mostrar_detalhe(iid):
        eid = iid_para_id.get(iid)
        empresa = empresas_por_id.get(eid) if eid is not None else None
        if not empresa:
            return
        nfe = empresa.get("ultima_sync_mensagem") or "sem registro de NF-e"
        detalhe.set(f"NF-e: {nfe}")
        if empresa.get("sync_ativo", True):
            botao_empresa.set("Pausar empresa selecionada")
        else:
            botao_empresa.set("Retomar empresa selecionada")

    def _cfg():
        return load_config()

    def confirmar_parada(titulo, mensagem):
        dlg = tk.Toplevel(root)
        dlg.title(titulo)
        dlg.transient(root)
        dlg.resizable(False, False)
        escolha = {"ok": False, "auto": True}
        automatico = tk.BooleanVar(value=True)
        ttk.Label(dlg, text=mensagem, wraplength=420, justify="left").pack(
            anchor="w", padx=16, pady=(16, 8)
        )
        ttk.Checkbutton(
            dlg,
            text="Retomar automaticamente em 2 horas",
            variable=automatico,
        ).pack(anchor="w", padx=16, pady=(0, 12))
        botoes = ttk.Frame(dlg)
        botoes.pack(fill="x", padx=16, pady=(0, 16))

        def confirmar():
            escolha["ok"] = True
            escolha["auto"] = bool(automatico.get())
            dlg.destroy()

        def cancelar():
            dlg.destroy()

        ttk.Button(botoes, text="Cancelar", command=cancelar).pack(side="right")
        ttk.Button(botoes, text="Parar agora", command=confirmar).pack(side="right", padx=(0, 8))
        dlg.protocol("WM_DELETE_WINDOW", cancelar)
        dlg.grab_set()
        dlg.update_idletasks()
        x = root.winfo_rootx() + max(0, (root.winfo_width() - dlg.winfo_width()) // 2)
        y = root.winfo_rooty() + 80
        dlg.geometry(f"+{x}+{y}")
        root.wait_window(dlg)
        return escolha

    def entrar():
        global rodando, buscas_ativas
        try:
            dados = login(base.get().strip(), email.get().strip(), senha.get())
        except Exception as exc:
            messagebox.showerror("Login", str(exc))
            return
        senha.set("")
        save_config(
            dados.get("token") or "",
            base.get().strip() or DEFAULT_BASE_URL,
            email.get().strip(),
        )
        buscas_ativas = bool(dados.get("agente_ativo"))
        modo_sessao(True)
        status.set("Conectado. O certificado continua no servidor.")
        if not rodando:
            rodando = True
            threading.Thread(target=loop, args=(atualizar,), daemon=True).start()
        atualizar(dados.get("empresas") or [])

    def sair():
        global rodando, buscas_ativas
        rodando = False
        buscas_ativas = False
        clear_session()
        tree.delete(*tree.get_children())
        banner.pack_forget()
        detalhe.set("Selecione uma empresa para ver o resultado da última NF-e.")
        modo_sessao(False)
        status.set("Sessão encerrada neste computador. Informe a senha para entrar de novo.")
        atualizar_bandeja([])

    def alternar_buscas():
        if buscas_ativas:
            parar_buscas()
        else:
            iniciar_buscas()

    def iniciar_buscas():
        global buscas_ativas
        cfg = _cfg()
        token = cfg.get("token")
        if not token:
            messagebox.showwarning("Buscas", "Faça login primeiro.")
            return
        try:
            sync_iniciar(cfg.get("base_url") or DEFAULT_BASE_URL, token)
            buscas_ativas = True
            empresas = listar_empresas(cfg.get("base_url") or DEFAULT_BASE_URL, token)
            atualizar(empresas)
        except Exception as exc:
            messagebox.showerror("Iniciar buscas", str(exc))

    def parar_buscas():
        global buscas_ativas
        cfg = _cfg()
        token = cfg.get("token")
        if not token:
            messagebox.showwarning("Buscas", "Faça login primeiro.")
            return
        escolha = confirmar_parada(
            "Parar buscas",
            "As buscas de todas as empresas param agora. "
            "A faixa de atenção continua visível para ninguém esquecer de religar.",
        )
        if not escolha["ok"]:
            return
        try:
            sync_parar(
                cfg.get("base_url") or DEFAULT_BASE_URL,
                token,
                escolha["auto"],
            )
            buscas_ativas = False
            empresas = listar_empresas(cfg.get("base_url") or DEFAULT_BASE_URL, token)
            atualizar(empresas)
        except Exception as exc:
            messagebox.showerror("Parar buscas", str(exc))

    def aplicar_pausa(eid: int, novo: bool, automatico: bool):
        cfg = _cfg()
        token = cfg.get("token")
        if not token:
            return
        try:
            empresa_sync(
                cfg.get("base_url") or DEFAULT_BASE_URL,
                token,
                eid,
                novo,
                automatico,
            )
            empresas = listar_empresas(cfg.get("base_url") or DEFAULT_BASE_URL, token)
            atualizar(empresas)
        except Exception as exc:
            messagebox.showerror("Pausar ou retomar", str(exc))

    def toggle_empresa(eid=None):
        if eid is None:
            sel = tree.selection()
            if not sel:
                messagebox.showinfo("Empresa", "Selecione uma empresa na lista.")
                return
            eid = iid_para_id.get(sel[0])
        if eid is None:
            return
        with _lock:
            atual = next((e for e in _empresas_cache if e.get("id") == eid), None)
        if atual is None:
            atual = empresas_por_id.get(eid)
        ativo_atual = True if not atual else bool(atual.get("sync_ativo", True))
        novo = not ativo_atual
        automatico = True
        if not novo:
            nome = (atual or {}).get("razao_social") or "esta empresa"
            escolha = confirmar_parada(
                "Pausar empresa",
                f"A busca de {nome} para agora. "
                "A linha fica marcada e a faixa de atenção permanece até a retomada.",
            )
            if not escolha["ok"]:
                return
            automatico = escolha["auto"]
        aplicar_pausa(eid, novo, automatico)

    def ao_clicar(event):
        if tree.identify_region(event.x, event.y) != "cell":
            return
        if tree.identify_column(event.x) != "#8":
            return
        iid = tree.identify_row(event.y)
        if not iid:
            return
        eid = iid_para_id.get(iid)
        if eid is None:
            return
        toggle_empresa(eid)

    def ao_selecionar(_event):
        sel = tree.selection()
        if sel:
            mostrar_detalhe(sel[0])

    tree.bind("<ButtonRelease-1>", ao_clicar)
    tree.bind("<<TreeviewSelect>>", ao_selecionar)

    def ao_fechar():
        root.withdraw()

    def encerrar():
        global rodando
        rodando = False
        _instancia.liberar()
        root.destroy()

    def criar_bandeja():
        global _tray
        try:
            import pystray
            from PIL import Image, ImageDraw

            img = Image.new("RGB", (64, 64), color="#1e3a8a")
            draw = ImageDraw.Draw(img)
            draw.rectangle((8, 8, 56, 56), outline="white", width=4)
            menu = pystray.Menu(
                pystray.MenuItem("Abrir", lambda i, m: root.after(0, _mostrar), default=True),
                pystray.MenuItem("Sair", lambda i, m: root.after(0, encerrar)),
            )
            icone = pystray.Icon("beatle_contador", img, "Beatle Contador", menu)
            _tray = icone
            icone.run()
        except Exception:
            pass

    root.protocol("WM_DELETE_WINDOW", ao_fechar)
    threading.Thread(target=criar_bandeja, daemon=True).start()

    if cfg0.get("token"):
        rodando = True
        modo_sessao(True)
        threading.Thread(target=loop, args=(atualizar,), daemon=True).start()
        status.set("Sessão salva. O painel atualiza sozinho.")

    root.mainloop()
    rodando = False
    _instancia.liberar()


def _avisar_ja_aberto():
    try:
        import tkinter as tk
        from tkinter import messagebox

        aviso = tk.Tk()
        aviso.withdraw()
        messagebox.showinfo(
            "Beatle Contador",
            "O Beatle Contador já está aberto nesta máquina. "
            "Use a janela que está na bandeja.",
        )
        aviso.destroy()
    except Exception:
        pass


if __name__ == "__main__":
    main()
