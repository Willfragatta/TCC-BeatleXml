# Beatle Uploader

Aplicativo para upload automático de NFe e NFCe emitidas para o Beatle_XML.

## Desenvolvimento

```bash
# Na raiz do projeto
pip install -r beatle_uploader/requirements.txt
python -m beatle_uploader.main
```

### Pastas / Upload

Ao **Iniciar monitoramento**:
1. Varre todos os `.xml` já existentes nas pastas NFe/NFCe e envia (novos no banco ou atualização).
2. Continua observando a pasta (watchdog) para XMLs que chegarem depois.

A empresa do combo define o CNPJ emitente aceito no upload.

### Aba ERP / Status notas

Opcional. Conecta no PostgreSQL do Conexo e atualiza no site as colunas Baixada (PBX), Recebida (RCM), Lançada e Transação das notas RECEBIDAS de **todas as empresas** do usuário logado (não só a selecionada em Pastas).

1. Login na aba Pastas / Upload
2. Em **ERP / Status notas**, preencha host/porta/db/usuário/senha do ERP
3. Testar conexão → Ativar → Iniciar ERP
4. No site, em **/config**, marque **Exibir status Conexo em /notas**

## Build (gerar executável Windows)

Na raiz do projeto:

```bash
# Windows
tools\build_uploader.bat

# Ou manualmente:
pip install pyinstaller requests watchdog
pyinstaller --onefile --windowed --name BeatleUploader ^
    --hidden-import=beatle_uploader.config ^
    --hidden-import=beatle_uploader.auth ^
    --hidden-import=beatle_uploader.monitor ^
    --hidden-import=beatle_uploader.uploader ^
    -p . ^
    beatle_uploader/main.py
```

O executável será gerado em `dist/BeatleUploader.exe`. Copie para `static/releases/BeatleUploader-1.0.0.exe` para disponibilizar no site.
