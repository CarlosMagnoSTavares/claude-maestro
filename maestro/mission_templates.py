"""Modelos de missão prontos — resolve a "página em branco".

Curadoria para pequenas empresas brasileiras: o gestor escolhe um modelo,
o texto da missão vem pré-preenchido (e editável). Usado pela UI web
(GET /api/mission-templates) e pelo bot do WhatsApp.
"""

TEMPLATES = [
    {
        "id": "site-institucional",
        "icon": "fa-globe",
        "category": "Presença digital",
        "title": "Criar site da minha empresa",
        "mission": (
            "Crie um site institucional completo para a minha empresa, em português do Brasil, "
            "como página única (index.html) moderna e responsiva que funcione abrindo o arquivo "
            "direto no navegador (sem servidor). Use as informações da empresa do CLAUDE.md. "
            "Inclua: topo com nome e slogan, seção 'quem somos', lista de produtos/serviços, "
            "depoimentos de exemplo, botão de WhatsApp e rodapé com contatos. "
            "Cores profissionais e fontes legíveis. Ao final, escreva um COMO_PUBLICAR.md "
            "explicando em linguagem simples como colocar o site no ar de graça."
        ),
    },
    {
        "id": "controle-financeiro",
        "icon": "fa-coins",
        "category": "Financeiro",
        "title": "Planilha de controle financeiro",
        "mission": (
            "Crie uma planilha Excel (.xlsx) de controle financeiro mensal para pequena empresa, "
            "em português: abas de Entradas, Saídas, Resumo mensal com gráficos e Fluxo de caixa. "
            "Fórmulas prontas, categorias típicas (vendas, fornecedores, aluguel, impostos, salários), "
            "formatação com cores (verde receitas, vermelho despesas) e algumas linhas de exemplo. "
            "Gere também um LEIA-ME.md curto explicando como usar no dia a dia."
        ),
    },
    {
        "id": "analise-vendas",
        "icon": "fa-chart-line",
        "category": "Financeiro",
        "title": "Analisar minha planilha de vendas",
        "mission": (
            "Analise os arquivos de planilha (Excel/CSV) que estão na pasta deste projeto e produza "
            "um relatório em português (relatorio_vendas.html, visual e com gráficos) com: produtos que "
            "mais vendem, meses fortes e fracos, ticket médio, tendências e 5 recomendações práticas "
            "para vender mais. Se não houver planilha na pasta, crie uma planilha modelo de vendas e "
            "explique no relatório como preenchê-la para a próxima análise."
        ),
    },
    {
        "id": "posts-instagram",
        "icon": "fa-hashtag",
        "category": "Marketing",
        "title": "Calendário de posts para Instagram",
        "mission": (
            "Crie um calendário de conteúdo de 30 dias para o Instagram da minha empresa (use o "
            "CLAUDE.md para entender o negócio). Entregue calendario_posts.xlsx com: data, tema, "
            "legenda pronta (com emojis e hashtags brasileiras relevantes), sugestão de imagem e "
            "melhor horário. Misture posts de venda, dicas, bastidores e datas comemorativas do Brasil. "
            "Inclua um guia rápido GUIA_INSTAGRAM.md com boas práticas."
        ),
    },
    {
        "id": "pesquisa-concorrentes",
        "icon": "fa-binoculars",
        "category": "Estratégia",
        "title": "Pesquisa de concorrentes",
        "mission": (
            "Faça uma pesquisa de mercado sobre os concorrentes do meu segmento (use o CLAUDE.md "
            "para entender o negócio). Entregue concorrentes.html em português com: principais "
            "concorrentes, o que oferecem, faixas de preço praticadas, pontos fortes e fracos, "
            "e uma tabela comparativa. Feche com 5 oportunidades concretas para eu me diferenciar."
        ),
    },
    {
        "id": "proposta-comercial",
        "icon": "fa-file-signature",
        "category": "Vendas",
        "title": "Modelo de proposta comercial",
        "mission": (
            "Crie um modelo de proposta comercial profissional para a minha empresa: "
            "proposta_comercial.html pronto para imprimir/salvar em PDF, em português, com espaços "
            "claramente marcados para preencher (cliente, escopo, valores, prazo, condições). "
            "Visual limpo e profissional com os dados da empresa do CLAUDE.md. Inclua também uma "
            "versão em texto simples para enviar por WhatsApp e um checklist de follow-up de venda."
        ),
    },
    {
        "id": "atendimento-whatsapp",
        "icon": "fa-comments",
        "category": "Atendimento",
        "title": "Kit de respostas para WhatsApp",
        "mission": (
            "Crie um kit de atendimento por WhatsApp para minha empresa: respostas_whatsapp.md com "
            "mensagens prontas em português para as 20 situações mais comuns (saudação, preço, prazo, "
            "orçamento, reclamação, pós-venda, cobrança amigável, cliente sumido, agradecimento). "
            "Tom simpático e profissional, adaptado ao meu negócio (CLAUDE.md). Inclua dicas de quando "
            "usar cada mensagem e como configurar respostas rápidas no WhatsApp Business."
        ),
    },
    {
        "id": "relatorio-semanal",
        "icon": "fa-clipboard-list",
        "category": "Gestão",
        "title": "Relatório semanal do negócio (recorrente)",
        "mission": (
            "Monte um relatório semanal do negócio: analise os arquivos e planilhas da pasta do "
            "projeto e gere relatorio_semana.html em português com o resumo do que mudou, números "
            "importantes, pendências e sugestões de foco para a próxima semana. Se for a primeira vez, "
            "crie a estrutura de acompanhamento (planilha de indicadores) e explique como alimentá-la. "
            "Dica: agende esta missão na Agenda para rodar toda semana."
        ),
    },
]


def list_templates() -> list[dict]:
    return TEMPLATES


def get_template(tpl_id: str) -> dict | None:
    return next((t for t in TEMPLATES if t["id"] == tpl_id), None)
