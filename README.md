# Claude Maestro

**Plataforma local que transforma o Claude Code numa equipe de agentes operada por um gestor — sem terminal, com governança, heartbeat e auditoria.**

> 🌐 **Site de download:** https://carlosmagnostavares.github.io/claude-maestro/
> ⬇️ **Baixar (Windows):** [ClaudeMaestro-Setup.exe](https://github.com/CarlosMagnoSTavares/claude-maestro/releases/latest) · **grátis para usar** ([licença](LICENSE): Apache 2.0 + Commons Clause — usar é livre, **vender** exige licença comercial).

> 🚀 **Do mesmo autor: [Aurora](https://cmsttech.com.br/)**, o orquestrador profissional de agentes para o Claude Code (CTO de IA + 74 especialistas, grid de terminais, revisão antes do commit, WhatsApp e celular; Windows, Mac e Linux). [Guias de Claude Code em português](https://cmsttech.com.br/guias/).

Inspirado no núcleo do Paperclip (governança, heartbeat, tarefas, budget), reconstruído de forma simples e direta em Python — sem fork. O usuário descreve a empresa e a missão em português; o **Maestro** contrata os agentes certos, escolhe as skills mais populares do [skills.sh](https://skills.sh), planeja e executa — pedindo aprovação quando a governança exigir.

## Como usar

1. Duplo clique em `Iniciar_Maestro.bat` — a janela abre sozinha (interface SB Admin 2).
2. Primeira vez: siga o checklist do **Painel** (instalar Claude Code → modo automático → login).
3. **Empresas** → **Projetos** (escolha uma pasta) → **Missões** → Executar.
4. Se a missão for nível "Aprovação obrigatória" (padrão), o plano aparece em **Aprovações** — um clique resolve.

## Governança (níveis de autonomia por missão)

| Nível | Comportamento |
|---|---|
| 0 — Somente sugestão | Contrata equipe e gera o plano; **não executa nada** |
| 1 — Aprovação obrigatória *(padrão seguro)* | Gera o plano e **pausa até o gestor aprovar** |
| 3 — Autônomo | Executa sem aprovação; exige aceite do **Termo de Ciência e Assunção de Risco** (consentimento registrado com hash, data e computador) |

- **Budget hard-stop:** cada execução tem teto de custo (US$, configurável). Estourou → pausa e vira decisão em **Aprovações** (anti-loop).
- **Auditoria imutável:** todos os eventos são encadeados por hash (estilo blockchain). Verificação de integridade e exportação em **Ajustes**.

## Heartbeat (limite de uso do Claude)

Quando o Claude atinge o limite do plano:

1. O Maestro detecta a mensagem de limite e **extrai o horário de reset** (epoch `…|1751468400`, texto "resets at 7pm", ou fallback de N minutos).
2. A execução pausa como **Aguardando limite** — a micro-tarefa em andamento volta à fila, nada é perdido.
3. O **heartbeat** (verificação a cada 30s) **retoma sozinho** no horário do reset e continua de onde parou.
4. Funciona até se o app for fechado e reaberto: execuções órfãs são reagendadas no boot.

Banner no Painel e na missão mostram o horário da retomada; botão "Tentar agora" força imediato.

## Recursos

- **Equipe proativa (retrospectiva)** — ao concluir uma missão, o Maestro analisa os objetivos da empresa e as entregas e **sugere as próximas missões**; o gestor aceita com um clique na web ou pelo WhatsApp (menu 9). Desligável em Ajustes (`proactive_suggestions`).
- **Skills automáticas** — além do top do leaderboard, o Maestro **busca no skills.sh skills específicas para cada missão** (API de busca) e valida qualquer skill que a IA sugerir, com filtro de reputação. Zero trabalho manual de garimpar skills.
- **Modelos de missão prontos** — galeria curada para pequenas empresas (site, controle financeiro, análise de vendas, posts de Instagram, proposta comercial, kit de atendimento…): um clique preenche a missão, na web e no WhatsApp.
- **Entregas** — ao fim de cada execução, a missão mostra todos os arquivos produzidos/alterados na pasta do projeto, com download e botão "Abrir pasta"; o resumo das entregas também chega no WhatsApp.
- **Painel** — estado do sistema, missões ativas, decisões pendentes, custo acumulado, heartbeat.
- **Aprovações** — planos e liberações de budget; aprovar/rejeitar com um clique.
- **Equipe de Agentes** — agentes contratados (formato nativo `.claude/agents/*.md`), reaproveitados entre missões.
- **Agenda** — missões recorrentes; opcionalmente registradas no Agendador do Windows (rodam com o app fechado).
- **Exportar / Importar** — pacote .zip com tudo (empresas, projetos, missões, agentes, skills, histórico, auditoria, arquivos gerados) para migrar de máquina.
- **Ajustes** — modelos (haiku/sonnet/opus), autonomia padrão, teto de budget, heartbeat, paralelismo, reputação mínima de skills, auditoria.

## Requisitos

- Windows 10/11 · [Python 3.11+](https://www.python.org/downloads/) (PATH) · [Node.js LTS](https://nodejs.org) · conta no Claude.

## Para desenvolvedores

```bash
pip install -r requirements.txt
python -m maestro serve            # UI em http://127.0.0.1:8765
python -m maestro run-task <id>    # execução headless (Agendador do Windows)
```

Stack: FastAPI + SQLite (`data/maestro.db`) + frontend estático **SB Admin 2** (Bootstrap 4, assets locais em `maestro/static/vendor/` — funciona offline). Claude Code invocado headless: `claude -p --output-format json --dangerously-skip-permissions`.

```
maestro/
├── api.py             # REST + estáticos
├── orchestrator.py    # máquina de estados resumível: fases, ondas paralelas,
│                      #   heartbeat, aprovações, budget hard-stop
├── governance.py      # níveis de autonomia, termo de risco, consentimentos, aprovações
├── claude_cli.py      # headless + detecção de limite de uso (detect_limit)
├── maestro_brain.py   # recrutamento de agentes e planejamento (modo PLAN)
├── skills_catalog.py  # leaderboard skills.sh + npx skills add
├── scheduler.py       # agenda + tick do heartbeat (30s)
├── bootstrap.py       # instalação/detecção do Claude Code, CLAUDE.md por projeto
├── porter.py          # export/import total (.zip)
├── notify.py          # toast Windows + feed
├── db.py              # SQLite + migrações + trilha de auditoria encadeada por hash
└── static/            # SB Admin 2 (sem build): index.html, app.js, maestro.css, vendor/
```

## Estados de uma execução

```
contratando → habilidades → planejando → executando → concluída
                    │             │            │
                    │       aguardando_aprovacao (nível 1 / budget)
                    └────── aguardando_limite (heartbeat retoma no reset)
```

## Segurança

O modo de permissões automáticas usa `--dangerously-skip-permissions` dentro da pasta do projeto. O padrão de fábrica é governança **nível 1** (nada executa sem aprovação). Autonomia plena exige consentimento registrado. Budget hard-stop limita gasto por execução. Tudo fica auditável e exportável.
