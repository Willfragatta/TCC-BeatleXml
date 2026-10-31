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

Caso ainda não tenha o Docker no seu computador, você precisa fazer o download do cliente oficial:
• Baixe e instale o Docker Desktop para Windows https://www.docker.com/products/docker-desktop/.
• Durante a instalação, certifique-se de marcar a opção para ativar o WSL 2 (Windows Subsystem for Linux), que garante o melhor desempenho no Windows 11/10.
• Após instalar, reinicie o computador.
Se você acabou de instalar o Docker ou de iniciá-lo, o terminal que já estava aberto (como o do seu VS Code) não vai reconhecer o comando imediatamente.
• Feche completamente o VS Code ou o PowerShell.
• Reinicie o computador
• abra o aplicativo docker instalado e deixe no estado "Docker desktop is running"
• Abra-o novamente sua IDE e tente rodar o comando na pasta do projeto
• rode docker compose up --build

Se o Docker Desktop estiver aberto e mesmo assim o erro persistir, o instalador pode ter falhado ao adicionar o caminho do Docker no sistema.
1. Pressione a tecla Windows e digite Variáveis de Ambiente, selecionando "Editar as variáveis de ambiente do sistema".
2. Clique no botão Variáveis de Ambiente... na parte inferior.
3. Em "Variáveis do Sistema", procure pela linha Path e clique em Editar.
4. Certifique-se de que o seguinte caminho está na lista: C:\Program Files\Docker\Docker\resources\bin.
5. Se não estiver, clique em Novo e adicione-o manualmente. Dê OK em todas as janelas.

• Depois rode:

```text
docker compose up --build
```

• Na primeira subida o contêiner cria as tabelas e grava a carga fictícia. 
Para ver o worker (ele não consulta a SEFAZ sem certificado): se vc quiser testar com um certificado real, suba-o no sistema no menu empresas, depois vá até o menu Notas ficais e clique no botão "sincronizar".


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

O Beatle Uploader e o Beatle Contador são janelas do Windows. Eles ficam fora do Docker e falam com a aplicação em `http://localhost:8000`. Siga esta ordem. O site precisa já estar no ar (`docker compose up --build`) antes de abrir qualquer agente.

• Abra o PowerShell na raiz deste repositório, a pasta que contém `beatle_uploader` e `beatle_contador`. 
• Os comandos abaixo usam sempre `python -m pip` e `python` juntos. Assim o pacote entra no mesmo Python que vai executar o programa. Se a máquina tiver mais de um Python, um `pip install` solto instala num e o `python` roda o outro: o Uploader para na hora com `NameError: name 'FileSystemEventHandler' is not defined`, porque o `watchdog` não está naquele interpretador.

### 1. Conferir o Python

```text
python -c "import sys; print(sys.executable)"
python -m pip -V
```

As duas linhas precisam mostrar a mesma pasta, por exemplo `...\Python314\`. Se o `python` abrir a Microsoft Store, instale o Python em [python.org](https://www.python.org/downloads/windows/), marque **Add python.exe to PATH** e abra um PowerShell novo.

### 2. Instalar as dependências

• Ainda na raiz:

```text
python -m pip install -r beatle_uploader\requirements.txt
python -m pip install -r beatle_contador\requirements.txt
```

### 3. Rodar

Cada agente ocupa um terminal. Deixe os dois abertos enquanto testa. Continue na raiz. Entrar na pasta `beatle_uploader` e chamar `python -m beatle_uploader.main` não acha o módulo.

```text
python beatle_uploader\main.py
```

```text
python beatle_contador\main.py
```

No Uploader, a URL já vem `http://localhost:8000`. Entre com `gestor@demo.local` e `Gestor#2026`, escolha a empresa e aponte ao menos uma pasta. Para ver um envio, copie `scripts\fixtures\nfce-caixa.xml` para essa pasta e clique em **Iniciar monitoramento**. O XML de exemplo traz o CNPJ da matriz no emitente. Fechar a janela no X só esconde o programa na bandeja. Use **Parar** dentro da janela para interromper o envio.

No Contador, a URL também é `http://localhost:8000`. Entre com `contador@demo.local` e `Contador#2026`. O programa registra presença e pede a sincronização ao servidor. Quem consulta a SEFAZ, quando houver certificado, é o servidor.

### 4. Gerar o executável

Este passo é opcional. O caminho do item 3 já abre os dois agentes. Gere o `.exe` só quando quiser um arquivo para abrir com dois cliques, sem o terminal.

Os scripts `tools\build_uploader.bat` e `tools\build_contador.bat` não vêm neste pacote. Use o spec que está no repositório, no mesmo Python do item 1, ainda na raiz:

```text
python -m pip install pyinstaller
python -m PyInstaller beatle_uploader\build.spec
python -m PyInstaller beatle_contador\build.spec
```

Os arquivos saem em `dist\BeatleUploader.exe` e `dist\BeatleContador.exe`. Abra o `.exe` com o site no ar. O endereço padrão continua `http://localhost:8000` e os logins são os mesmos do item 3. Para o link de download do Contador no site, copie o executável para `static\releases\BeatleContador-1.1.0.exe`.

## E-mail, 2FA e reCAPTCHA

Confirmação de cadastro, recuperação de senha e código de 2FA aparecem no Mailpit. O 2FA começa desligado. Em Configurações dá para ligar e, no login seguinte, o código chega em <http://localhost:8025>.

O reCAPTCHA permanece nas telas de entrada. Nesta demonstração a chave fica vazia e o ambiente não é produção, então o formulário não espera o Google. Com internet, as chaves públicas de teste estão comentadas no `.env.example`.

## Limite da demonstração

A consulta viva à SEFAZ pede certificado A1 e rede. Aqui avaliamos com os XMLs de exemplo e com a importação manual. 

Com a aplicação no ar, `python scripts/verificar_fluxos.py` repete o login dos papéis, o isolamento, o ZIP, o DANFE, o envio do Uploader, o pedido do Contador e o 2FA no Mailpit. No fim, o 2FA do gestor volta a ficar desligado.
