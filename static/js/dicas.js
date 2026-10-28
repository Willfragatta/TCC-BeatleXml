/* Tooltips leves para botões com data-dica (hover ~700ms). */
(function () {
    const DELAY_MS = 700;
    let timer = null;
    let tip = null;
    let ligado = true;

    function garantirTip() {
        if (tip) return tip;
        tip = document.createElement('div');
        tip.id = 'beatle-dica-tip';
        tip.style.cssText = [
            'position:fixed', 'z-index:9999', 'max-width:280px',
            'padding:8px 10px', 'border-radius:8px', 'font-size:12px',
            'line-height:1.35', 'background:#0f172a', 'color:#f8fafc',
            'box-shadow:0 8px 24px rgba(15,23,42,.25)', 'pointer-events:none',
            'opacity:0', 'transition:opacity .12s ease', 'display:none',
        ].join(';');
        document.body.appendChild(tip);
        return tip;
    }

    function esconder() {
        if (timer) { clearTimeout(timer); timer = null; }
        if (!tip) return;
        tip.style.opacity = '0';
        tip.style.display = 'none';
    }

    function mostrar(el, ev) {
        if (!ligado) return;
        const texto = el.getAttribute('data-dica');
        if (!texto) return;
        const t = garantirTip();
        t.textContent = texto;
        t.style.display = 'block';
        const x = Math.min(window.innerWidth - 300, Math.max(8, (ev.clientX || 0) + 12));
        const y = Math.min(window.innerHeight - 80, Math.max(8, (ev.clientY || 0) + 16));
        t.style.left = x + 'px';
        t.style.top = y + 'px';
        requestAnimationFrame(() => { t.style.opacity = '1'; });
    }

    function onEnter(ev) {
        const el = ev.target.closest('[data-dica]');
        if (!el || !ligado) return;
        esconder();
        timer = setTimeout(() => mostrar(el, ev), DELAY_MS);
    }

    function onLeave(ev) {
        const el = ev.target.closest('[data-dica]');
        if (!el) return;
        esconder();
    }

    function aplicar() {
        // noop: delegation no document
    }

    function setLigado(v) {
        ligado = !!v;
        if (!ligado) esconder();
        document.documentElement.dataset.dicas = ligado ? '1' : '0';
    }

    document.addEventListener('mouseover', onEnter, true);
    document.addEventListener('mouseout', onLeave, true);
    document.addEventListener('click', esconder, true);

    window.BeatleDicas = { aplicar, setLigado, DELAY_MS };
})();
