"""Beatle Uploader - Aplicativo para upload automático de NFe/NFCe emitidas."""
import os
import sys
import logging
import threading
import time

# Permite rodar como script: python beatle_uploader/main.py (não altera em PyInstaller)
if getattr(sys, "frozen", False):
    pass  # PyInstaller: módulos já embutidos
elif __file__:
    _p = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _p not in sys.path:
        sys.path.insert(0, _p)

from beatle_uploader.config import load_config, save_config, update_config, DEFAULT_BASE_URL
from beatle_uploader.auth import login
from beatle_uploader.monitor import iniciar_monitor
from beatle_uploader.uploader import (
    processar_arquivo,
    enviar_heartbeat,
    registrar_detectado,
    registrar_resultado,
    obter_estado_sessao,
    resetar_estado_sessao,
    varrer_e_enviar_pastas,
)

LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "beatle_uploader.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("beatle_uploader")

observers = []
monitorando = False


def on_novo_xml(path: str):
    """Callback quando novo XML é detectado (NFe ou NFCe)."""
    cfg = load_config()
    if not cfg.get("token"):
        logger.warning("Sem token configurado. Faça login.")
        return
    registrar_detectado(path)
    ok = processar_arquivo(cfg, path)
    registrar_resultado(path, ok)


def loop_heartbeat():
    """Thread que envia heartbeat a cada 5 minutos."""
    global monitorando
    while monitorando:
        time.sleep(300)  # 5 minutos
        if not monitorando:
            break
        cfg = load_config()
        if cfg.get("token"):
            enviar_heartbeat(cfg)


def _empresas_para_erp(cfg: dict) -> list:
    """Lista de empresas do login; fallback para a empresa selecionada."""
    empresas = cfg.get("empresas") or []
    if isinstance(empresas, list) and empresas:
        return [e for e in empresas if isinstance(e, dict) and e.get("id") is not None]
    if cfg.get("empresa_id"):
        return [{
            "id": int(cfg["empresa_id"]),
            "razao_social": "Empresa selecionada",
            "cnpj": cfg.get("cnpj_empresa") or "",
        }]
    return []


def _varrer_pastas_inicial():
    """Varre pastas NFe/NFCe e envia XMLs já existentes (além dos novos via watchdog)."""
    cfg = load_config()
    pasta_nfe = (cfg.get("pasta_nfe") or cfg.get("pasta") or "").strip()
    pasta_nfce = (cfg.get("pasta_nfce") or "").strip()
    pastas = [p for p in (pasta_nfe, pasta_nfce) if p]
    if not pastas:
        return
    try:
        res = varrer_e_enviar_pastas(cfg, pastas)
        logger.info(
            "Varredura inicial: %s encontrado(s), %s ok, %s erro(s)",
            res.get("encontrados"),
            res.get("enviados_ok"),
            res.get("erros"),
        )
    except Exception as e:
        logger.warning("Varredura inicial falhou: %s", e)


def iniciar():
    global observers, monitorando
    cfg = load_config()
    if not cfg.get("token") or not cfg.get("empresa_id"):
        return False, "Configure login e empresa primeiro."

    pasta_nfe = (cfg.get("pasta_nfe") or cfg.get("pasta") or "").strip()
    pasta_nfce = (cfg.get("pasta_nfce") or "").strip()
    if not pasta_nfe and not pasta_nfce:
        return False, "Informe ao menos uma pasta (NFe e/ou NFCe)."

    monitorando = True
    th = threading.Thread(target=loop_heartbeat, daemon=True)
    th.start()

    observers.clear()
    if pasta_nfe:
        ob = iniciar_monitor(pasta_nfe, on_novo_xml)
        if ob:
            observers.append(ob)
        else:
            monitorando = False
            return False, f"Erro ao monitorar pasta NFe: {pasta_nfe}"
    if pasta_nfce:
        ob = iniciar_monitor(pasta_nfce, on_novo_xml)
        if ob:
            observers.append(ob)
        else:
            for o in observers:
                o.stop()
                o.join(timeout=3)
            observers.clear()
            monitorando = False
            return False, f"Erro ao monitorar pasta NFCe: {pasta_nfce}"

    if not observers:
        monitorando = False
        return False, "Nenhuma pasta válida para monitorar."

    resetar_estado_sessao()
    # Varre XMLs já existentes na pasta (não só arquivos novos)
    threading.Thread(target=_varrer_pastas_inicial, daemon=True).start()
    return True, "Monitoramento iniciado: varredura da pasta + novos XMLs."


def parar():
    global observers, monitorando
    monitorando = False
    for ob in observers:
        try:
            ob.stop()
            ob.join(timeout=5)
        except Exception:
            pass
    observers.clear()


def run_gui():
    """Interface gráfica com tkinter."""
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox, filedialog
    except ImportError:
        logger.error("tkinter não disponível")
        return

    root = tk.Tk()
    root.title("Beatle Uploader")
    root.geometry("560x580")
    root.resizable(True, True)

    cfg = load_config()
    base_url_var = tk.StringVar(value=cfg.get("base_url", DEFAULT_BASE_URL))
    email_var = tk.StringVar(value="")
    senha_var = tk.StringVar(value="")
    empresa_var = tk.StringVar(value="")
    pasta_nfe_var = tk.StringVar(value=cfg.get("pasta_nfe") or cfg.get("pasta") or "")
    pasta_nfce_var = tk.StringVar(value=cfg.get("pasta_nfce") or "")
    status_var = tk.StringVar(value="Pronto")
    contador_var = tk.StringVar(value="—")

    empresas_list = []

    def do_login():
        email = email_var.get().strip()
        senha = senha_var.get()
        base_url = base_url_var.get().strip()
        if not email or not senha:
            messagebox.showerror("Erro", "Informe email e senha.")
            return
        try:
            data = login(base_url, email, senha)
            token = data["token"]
            empresas = data.get("empresas", [])
            if not empresas:
                messagebox.showerror("Erro", "Nenhuma empresa encontrada.")
                return
            empresas_list.clear()
            empresas_list.extend(empresas)
            emp_combo["values"] = [f"{e['razao_social']} ({e['cnpj']})" for e in empresas]
            emp_combo.current(0)
            save_config(
                token, empresas[0]["id"],
                base_url=base_url,
                pasta_nfe=pasta_nfe_var.get(),
                pasta_nfce=pasta_nfce_var.get(),
                cnpj_empresa=empresas[0].get("cnpj") or "",
                empresas=empresas,
            )
            status_var.set(f"Login OK — {len(empresas)} empresa(s).")
        except Exception as e:
            messagebox.showerror("Erro", f"Falha no login: {e}")

    def escolher_pasta_nfe():
        p = filedialog.askdirectory(title="Selecionar pasta dos XMLs de NFe (modelo 55)")
        if p:
            pasta_nfe_var.set(p)

    def escolher_pasta_nfce():
        p = filedialog.askdirectory(title="Selecionar pasta dos XMLs de NFCe (modelo 65)")
        if p:
            pasta_nfce_var.set(p)

    def do_iniciar():
        if not empresas_list:
            messagebox.showwarning("Aviso", "Faça login primeiro.")
            return
        idx = emp_combo.current()
        if idx < 0:
            messagebox.showwarning("Aviso", "Selecione a empresa.")
            return
        empresa_id = empresas_list[idx]["id"]
        pasta_nfe = pasta_nfe_var.get().strip()
        pasta_nfce = pasta_nfce_var.get().strip()
        if not pasta_nfe and not pasta_nfce:
            messagebox.showerror("Erro", "Informe ao menos uma pasta (NFe e/ou NFCe).")
            return
        if pasta_nfe and not os.path.isdir(pasta_nfe):
            messagebox.showerror("Erro", "Pasta NFe inválida.")
            return
        if pasta_nfce and not os.path.isdir(pasta_nfce):
            messagebox.showerror("Erro", "Pasta NFCe inválida.")
            return
        c_atual = load_config()
        save_config(
            c_atual.get("token", ""), empresa_id,
            base_url=base_url_var.get().strip(),
            pasta_nfe=pasta_nfe,
            pasta_nfce=pasta_nfce,
            cnpj_empresa=empresas_list[idx].get("cnpj") or "",
            empresas=list(empresas_list),
        )
        ok, msg = iniciar()
        if ok:
            status_var.set("Monitorando... Varredura + novos XMLs.")
            contador_var.set("Ocioso — 0 enviadas nesta sessão")
            log_text.config(state=tk.NORMAL)
            log_text.delete("1.0", tk.END)
            log_text.insert(
                tk.END,
                "Monitoramento iniciado.\n"
                "1) Varredura: envia XMLs já existentes nas pastas.\n"
                "2) Em seguida observa novos arquivos que chegarem.\n",
            )
            log_text.config(state=tk.DISABLED)
            btn_iniciar.config(state="disabled")
            btn_parar.config(state="normal")
            iniciar_timer_ui()
        else:
            messagebox.showerror("Erro", msg)

    def do_parar():
        parar()
        status_var.set("Parado.")
        btn_iniciar.config(state="normal")
        btn_parar.config(state="disabled")

    root.geometry("600x640")
    nb = ttk.Notebook(root)
    nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    frame = ttk.Frame(nb, padding=16)
    nb.add(frame, text="Pastas / Upload")

    ttk.Label(frame, text="URL da API:").grid(row=0, column=0, sticky="w", pady=2)
    ttk.Entry(frame, textvariable=base_url_var, width=45).grid(row=0, column=1, padx=5, pady=2)

    ttk.Label(frame, text="Email:").grid(row=1, column=0, sticky="w", pady=2)
    ttk.Entry(frame, textvariable=email_var, width=45).grid(row=1, column=1, padx=5, pady=2)

    ttk.Label(frame, text="Senha:").grid(row=2, column=0, sticky="w", pady=2)
    ttk.Entry(frame, textvariable=senha_var, width=45, show="*").grid(row=2, column=1, padx=5, pady=2)

    ttk.Button(frame, text="Login", command=do_login).grid(row=3, column=1, sticky="w", pady=5)

    ttk.Label(frame, text="Empresa:").grid(row=4, column=0, sticky="w", pady=2)
    emp_combo = ttk.Combobox(frame, textvariable=empresa_var, width=42, state="readonly")
    emp_combo.grid(row=4, column=1, padx=5, pady=2)

    ttk.Label(frame, text="Pasta NFe (55):").grid(row=5, column=0, sticky="w", pady=2)
    row5 = ttk.Frame(frame)
    row5.grid(row=5, column=1, sticky="w", pady=2)
    ttk.Entry(row5, textvariable=pasta_nfe_var, width=35).pack(side=tk.LEFT)
    ttk.Button(row5, text="...", command=escolher_pasta_nfe, width=3).pack(side=tk.LEFT, padx=2)

    ttk.Label(frame, text="Pasta NFCe (65):").grid(row=6, column=0, sticky="w", pady=2)
    row6 = ttk.Frame(frame)
    row6.grid(row=6, column=1, sticky="w", pady=2)
    ttk.Entry(row6, textvariable=pasta_nfce_var, width=35).pack(side=tk.LEFT)
    ttk.Button(row6, text="...", command=escolher_pasta_nfce, width=3).pack(side=tk.LEFT, padx=2)

    btn_frame = ttk.Frame(frame)
    btn_frame.grid(row=7, column=1, sticky="w", pady=15)
    btn_iniciar = ttk.Button(btn_frame, text="Iniciar monitoramento", command=do_iniciar)
    btn_iniciar.pack(side=tk.LEFT, padx=2)
    btn_parar = ttk.Button(btn_frame, text="Parar", command=do_parar, state="disabled")
    btn_parar.pack(side=tk.LEFT, padx=2)

    ttk.Label(frame, text="Status:", font=("", 9, "bold")).grid(row=10, column=0, sticky="nw", pady=2)
    ttk.Label(frame, textvariable=status_var, foreground="gray").grid(row=10, column=1, sticky="w", pady=2)
    ttk.Label(frame, text="Envio:", font=("", 9, "bold")).grid(row=11, column=0, sticky="nw", pady=2)
    ttk.Label(frame, textvariable=contador_var, foreground="navy").grid(row=11, column=1, sticky="w", pady=2)

    log_frame = ttk.LabelFrame(frame, text="Log (tempo real)")
    log_frame.grid(row=12, column=0, columnspan=2, sticky="nsew", pady=10, padx=0)
    frame.columnconfigure(1, weight=1)
    frame.rowconfigure(12, weight=1)
    log_text = tk.Text(log_frame, height=8, width=60, state=tk.DISABLED, wrap=tk.WORD, font=("Consolas", 9))
    log_scroll = ttk.Scrollbar(log_frame)
    log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
    log_text.config(yscrollcommand=log_scroll.set)
    log_scroll.config(command=log_text.yview)

    def atualizar_ui_sessao():
        if not monitorando:
            return
        est = obter_estado_sessao()
        d, e, err, enviando = est["detectadas"], est["enviadas"], est["erros"], est["enviando"]
        if enviando:
            contador_var.set(f"Enviando... {e} de {d} enviadas (erros: {err})")
        else:
            contador_var.set(f"Ocioso — {e} enviadas nesta sessão" + (f" ({err} erros)" if err else ""))
        if est["log"]:
            log_text.config(state=tk.NORMAL)
            log_text.delete("1.0", tk.END)
            log_text.insert(tk.END, "\n".join(est["log"]) + "\n")
            log_text.see(tk.END)
            log_text.config(state=tk.DISABLED)
        root.after(400, atualizar_ui_sessao)

    def iniciar_timer_ui():
        root.after(400, atualizar_ui_sessao)

    if cfg.get("token") and (cfg.get("empresas") or cfg.get("empresa_id")):
        try:
            salvas = cfg.get("empresas") or []
            if isinstance(salvas, list) and salvas:
                empresas_list.extend(salvas)
                emp_combo["values"] = [f"{e.get('razao_social','')} ({e.get('cnpj','')})" for e in salvas]
            else:
                empresas_list.extend([{
                    "id": cfg["empresa_id"],
                    "razao_social": "Salvo",
                    "cnpj": cfg.get("cnpj_empresa") or "",
                }])
                emp_combo["values"] = ["Empresa configurada"]
            emp_combo.current(0)
        except Exception:
            pass

    tray_icon = None

    def mostrar_janela():
        root.after(0, lambda: (root.deiconify(), root.lift()))

    def parar_e_fechar():
        def _():
            if messagebox.askyesno("Parar Beatle Uploader", "Parar interromperá o envio de notas. Deseja realmente parar?"):
                if tray_icon and hasattr(tray_icon, "stop"):
                    try:
                        tray_icon.stop()
                    except Exception:
                        pass
                parar()
                root.destroy()
                import sys
                sys.exit(0)
        root.after(0, _)

    def criar_tray():
        try:
            import pystray
            from PIL import Image
            img = Image.new("RGB", (64, 64), color="blue")
            menu = pystray.Menu(
                pystray.MenuItem("Abrir janela", lambda i, m: mostrar_janela(), default=True),
                pystray.MenuItem("Parar programa", lambda i, m: parar_e_fechar()),
            )
            nonlocal tray_icon
            tray_icon = pystray.Icon("beatle_uploader", img, "Beatle Uploader", menu)
            tray_icon.run()
        except Exception as e:
            logger.warning("Tray não disponível: %s", e)

    def on_closing():
        root.withdraw()
        t = threading.Thread(target=criar_tray, daemon=True)
        t.start()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()


if __name__ == "__main__":
    run_gui()
