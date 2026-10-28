# BeatleXML — pacote da banca

Recorte do sistema para a defesa. Sobe na máquina local, com dados fictícios. Não usa o banco de produção e não traz certificado digital de cliente.

A pasta `tcc overleaf` fica só no disco, para envio ao Overleaf. Ela não entra no Git.

## O que sobe

Três serviços no Docker Compose:

- a aplicação em <http://localhost:8000>
- o PostgreSQL 16, com o esquema `nfe_app` criado na subida
- o Mailpit, caixa de e-mail local em <http://localhost:8025> (SMTP na porta 1025)

O worker que consultaria a SEFAZ fica num perfil separado e não sobe junto. As empresas de demonstração não têm certificado A1, então esse ciclo não chama a Receita.

## Como subir

É preciso o Docker Desktop.

```text
docker compose up --build
```

Na primeira subida o contêiner cria as tabelas e grava a carga fictícia. Para ver o worker (ele não consulta a SEFAZ sem certificado):

```text
docker compose --profile sefaz up
```

## Quem entra

As senhas abaixo são só desta demonstração.

| E-mail | Senha | O que vê |
| --- | --- | --- |
| gestor@demo.local | Gestor#2026 | Dono da matriz, da filial e da papelaria. Papel master. |
| operador@demo.local | Operador#2026 | Operador só da matriz. |
| leitura@demo.local | Leitura#2026 | Somente leitura da matriz. |
| contador@demo.local | Contador#2026 | Matriz, filial e papelaria. Não vê a Padaria Isolada. |
| suporte@demo.local | Suporte#2026 | Suporte. Vê as quatro empresas e o registro de ações. |
| outro@demo.local | Outro#2026 | Dono só da Padaria Isolada, para mostrar o isolamento. |

A matriz já tem uma NF-e de entrada, com duas parcelas em aberto no financeiro, uma NFC-e emitida e a etiqueta Conferir. CNPJs, nomes e XMLs são inventados.

## Agentes no Windows

Os programas de mesa não vão dentro do Docker: usam janela e pasta do Windows. No PowerShell, na raiz deste repositório, com a aplicação já no ar:

```text
pip install -r beatle_uploader\requirements.txt
python beatle_uploader\main.py
```

```text
pip install -r beatle_contador\requirements.txt
python beatle_contador\main.py
```

O endereço padrão dos dois é `http://localhost:8000`. No Uploader, entre com `gestor@demo.local` e aponte a pasta para um diretório seu. O arquivo `scripts/fixtures/nfce-caixa.xml` tem o CNPJ da matriz no emitente; copie-o para a pasta observada para ver o envio. O Beatle Contador entra com `contador@demo.local`, registra presença e pede a sincronização. Quem fala com a SEFAZ, quando houver certificado, é o servidor.

## E-mail, 2FA e reCAPTCHA

Confirmação de cadastro, recuperação de senha e código de 2FA aparecem no Mailpit. O 2FA começa desligado. Em Configurações dá para ligar e, no login seguinte, o código chega em <http://localhost:8025>.

O reCAPTCHA permanece nas telas de entrada. Nesta demonstração a chave fica vazia e o ambiente não é produção, então o formulário não espera o Google. Com internet, as chaves públicas de teste estão comentadas no `.env.example`.

## Limite da demonstração

A consulta viva à SEFAZ pede certificado A1 e rede. A banca avalia com os XMLs de exemplo e com a importação manual. Não há NFS-e, conciliação de cartão nem cobrança. Não há executável apontando para o servidor real.

Com a aplicação no ar, `python scripts/verificar_fluxos.py` repete o login dos papéis, o isolamento, o ZIP, o DANFE, o envio do Uploader, o pedido do Contador e o 2FA no Mailpit. No fim, o 2FA do gestor volta a ficar desligado.
