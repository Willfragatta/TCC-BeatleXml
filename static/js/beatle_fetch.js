/* Injeta header anti-CSRF em fetch do painel (X-Requested-With: BeatleXML). */
(function () {
    if (typeof window === 'undefined' || !window.fetch) return;
    const original = window.fetch.bind(window);
    window.fetch = function (input, init) {
        const opts = init ? { ...init } : {};
        const headers = new Headers(opts.headers || {});
        if (!headers.has('X-Requested-With')) {
            headers.set('X-Requested-With', 'BeatleXML');
        }
        opts.headers = headers;
        return original(input, opts);
    };
})();
