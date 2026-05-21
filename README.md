# BeatleXML

Sistema web para automação da captura e organização de documentos fiscais e bancários, desenvolvido como Trabalho de Conclusão de Curso no IFPR Campus Palmas (Bacharelado em Sistemas de Informação).

O BeatleXML centraliza XMLs, OFX e PDFs por empresa em um repositório em nuvem, reduz a troca manual de arquivos entre empresas e escritórios contábeis e apoia a conferência antecipada de notas de entrada. A plataforma combina consulta automatizada à SEFAZ (via certificado digital) com o agente desktop Beatle Uploader (monitoramento de pastas locais).

Autor: Wilian Fragata dos Santos  
Orientação: Claudia Bocchese de Lima, Eduardo Luiz Alba

## Situação do projeto (cronograma TCC)

| Etapa | Status |
|-------|--------|
| Análise de Requisitos e Regras de Negócio | Concluída |
| Prototipação e Modelagem do Sistema | Concluída |
| Desenvolvimento e Programação | Em andamento (este repositório) |
| Controle de Qualidade, Testes e Implantação | Planejada |

O código atual representa o início da fase de desenvolvimento: estrutura de pastas, modelos de dados alinhados ao diagrama entidade-relacionamento, API REST básica, esqueleto dos workers e do uploader. Integrações com SEFAZ, autenticação JWT e persistência em PostgreSQL serão implementadas nas próximas iterações.

## Stack tecnológica

- Python 3.11+
- FastAPI e Uvicorn (backend)
- Jinja2 (templates HTML)
- SQLAlchemy e PostgreSQL (persistência)
- Workers em segundo plano (`worker.py`, `worker_resumos.py`)
- Beatle Uploader (agente desktop, pasta `uploader/`)
- Produção prevista: Nginx, Certbot, systemd (Linux)

Detalhamento completo das ferramentas na Tabela 2 do artigo do TCC.

## Estrutura do repositório

```
TCC-BeatleXml/
├── backend/
│   ├── app/
│   │   ├── main.py              # Aplicação FastAPI
│   │   ├── config.py            # Variáveis de ambiente
│   │   ├── database.py          # SQLAlchemy
│   │   ├── models/              # Empresa, Usuario, Certificado, Documento
│   │   ├── schemas/             # Validação Pydantic
│   │   ├── routers/             # Rotas HTTP
│   │   ├── services/            # SEFAZ e XML (stubs)
│   │   └── templates/           # Interface web inicial
│   └── workers/                 # Sincronização SEFAZ (stubs)
├── uploader/
│   └── beatle_uploader/         # Agente de pastas locais (stub)
├── docs/
│   ├── requisitos.md            # RF/RNF resumidos
│   └── diagramas/               # PlantUML (ER e demais)
├── tests/                       # Testes automatizados iniciais
├── requirements.txt
└── .env.example
```

## Pré-requisitos

- Python 3.11 ou superior
- PostgreSQL (opcional neste protótipo; rotas de cadastro exigem banco configurado)

## Configuração local

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Edite o arquivo `.env` com a URL do banco quando for testar cadastros.

## Executar a API

Na pasta `backend`:

```bash
cd backend
uvicorn app.main:app --reload
```

Acesse:

- Interface: http://127.0.0.1:8000/
- Documentação da API: http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health

## Workers e uploader (protótipo)

```bash
cd backend
python -m workers.worker
python -m workers.worker_resumos

cd ..\uploader
python -m beatle_uploader.main
```

Os comandos acima apenas exibem mensagens de placeholder. A implementação completa está prevista para sprints seguintes.

## Testes

Na raiz do projeto:

```bash
pytest
```

## Documentação de apoio

- Requisitos resumidos: `docs/requisitos.md`
- Diagrama ER (PlantUML): `docs/diagramas/entidade_relacionamento.puml`
- Artigo completo e diagramas UML (casos de uso, classes, implantação, sequência): documento do TCC

## Próximos passos de desenvolvimento

1. Migrations e criação das tabelas no PostgreSQL
2. Autenticação de usuários (JWT)
3. Integração com Distribuição DF-e da SEFAZ no `SefazService` e no `worker.py`
4. Monitoramento de pastas no Beatle Uploader (watchdog)
5. Testes unitários de leitura de XML e certificado digital
6. Implantação em Linux com Nginx e HTTPS

## Licença e uso acadêmico

Projeto acadêmico. Código disponível para fins de avaliação do TCC e evolução incremental conforme o cronograma de banca.
