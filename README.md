# DIO Bootcamp Monitor

Monitora automaticamente novos bootcamps publicados no catálogo público da [DIO](https://www.dio.me/bootcamp), classifica a **chance de contratação** e envia notificações pelo **Telegram**.

---

## Sumário

- [Começando do zero](#começando-do-zero)
- [Objetivo](#objetivo)
- [Arquitetura](#arquitetura)
- [Instalação local](#instalação-local)
- [Criando o bot no Telegram](#criando-o-bot-no-telegram)
- [Obtendo o Chat ID](#obtendo-o-chat-id)
- [Configuração das variáveis de ambiente](#configuração-das-variáveis-de-ambiente)
- [Execução local](#execução-local)
- [GitHub Actions](#github-actions)
- [Situação da inscrição (prazo)](#situação-da-inscrição-prazo)
- [Interpretação das classificações](#interpretação-das-classificações)
- [Como funciona a persistência do JSON](#como-funciona-a-persistência-do-json)
- [Comportamento de primeira execução](#comportamento-de-primeira-execução)
- [Limitações conhecidas](#limitações-conhecidas)
- [Como ajustar palavras-chave e pontuação](#como-ajustar-palavras-chave-e-pontuação)
- [Depurando falhas comuns](#depurando-falhas-comuns)

---

## Começando do zero

Do repositório clonado até receber a primeira notificação.

### 1. Ambiente

```bash
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # Linux/macOS
pip install -r requirements.txt
pytest -q                    # deve passar tudo
```

### 2. Criar o bot

No Telegram, fale com o [@BotFather](https://t.me/BotFather) → `/newbot` → escolha
nome e username. Ele devolve um token no formato `123456789:AA...`.

### 3. Liberar o seu chat ← o passo que todo mundo esquece

Abra a conversa com o bot que você acabou de criar e envie **`/start`**.

Um bot do Telegram **não consegue iniciar conversa** com ninguém. Enquanto ele
nunca tiver recebido uma mensagem sua, todo envio falha com
`400 Bad Request: chat not found` — mesmo com token e chat ID corretos.

### 4. Descobrir o seu Chat ID

```bash
curl "https://api.telegram.org/bot<SEU_TOKEN>/getUpdates"
```

O número em `result[0].message.chat.id` é o seu chat ID. Se vier
`"result":[]`, o passo 3 não foi feito.

### 5. Configurar

```bash
cp .env.example .env          # Linux/macOS
Copy-Item .env.example .env   # Windows PowerShell
```

Edite o `.env` e preencha `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`. Não
renomeie o `.env.example` — copie, para manter o modelo no repositório.

### 6. Testar a conexão antes de valer

```bash
python -c "import sys; sys.path.insert(0,'src'); \
from config import load_config; from telegram_notifier import build_notifier, NewBootcampNotification; \
c=load_config(); n=build_notifier(c.telegram_bot_token, c.telegram_chat_id); \
print('enviado:', n.notify_new_bootcamp(NewBootcampNotification('Teste','','','TESTE',0,[],'ok','hoje'))); n.close()"
```

Se imprimir `enviado: True` e a mensagem chegar, está tudo certo. Se falhar, o
log diz exatamente o quê e o que fazer.

### 7. Primeira execução

```bash
INITIAL_NOTIFY=false MAX_DETAIL_PAGES=250 python src/main.py
```

Registra e classifica o catálogo inteiro sem notificar (leva ~10 min). Da
próxima vez em diante, só chega novidade de verdade.

### 8. Automatizar

Veja [GitHub Actions](#github-actions).

---

## Objetivo

Acompanhar o catálogo público da DIO diariamente, identificar bootcamps com **processo seletivo real para emprego** e notificar via Telegram antes que as inscrições encerrem.

O monitor **não faz login**, **não realiza matrícula** e **não contorna nenhum mecanismo de segurança**. Trabalha exclusivamente com conteúdo público entregue por requisições HTTP normais.

---

## Arquitetura

```
dio-bootcamp-monitor/
├── src/
│   ├── main.py              # Orquestrador: fluxo completo da execução
│   ├── dio_scraper.py       # Extração HTTP do catálogo e páginas de detalhe
│   ├── classifier.py        # Classificação contextual da chance de contratação
│   ├── telegram_notifier.py # Envio de notificações via Telegram Bot API
│   ├── storage.py           # Persistência atômica do histórico em JSON
│   └── config.py            # Leitura e validação das variáveis de ambiente
├── tests/
│   ├── test_classifier.py         # Classificação: falsos positivos, boilerplate, prazo
│   ├── test_storage.py            # Leitura/escrita/upsert do histórico
│   ├── test_scraper.py            # Scraper com HTML mockado
│   ├── test_config.py             # Parser de .env e precedência do ambiente
│   ├── test_telegram_notifier.py  # Envio, rate limit e throttle (rede mockada)
│   ├── test_main_flow.py          # Integração do fluxo principal
│   └── fixtures/                  # HTMLs para testes sem internet
├── data/
│   └── bootcamps.json       # Histórico persistido (commitado pelo Actions)
├── .github/workflows/
│   └── monitor.yml          # Workflow de execução diária
├── .env.example             # Modelo de variáveis de ambiente
├── requirements.txt
└── README.md
```

**Fluxo de execução:**

```
Carrega config → Carrega histórico JSON
    ↓
Busca catálogo público (HTTP)
    ↓
Detecta novos e alterações
    ↓
Busca detalhes (respeitando MAX_DETAIL_PAGES)
    ↓
Classifica com análise contextual
    ↓
Salva histórico (atômico)
    ↓
Envia notificações Telegram
    ↓
Envia resumo diário (opcional)
```

**Estratégias de scraping (em ordem de prioridade):**

1. **Caminho canônico do `__NEXT_DATA__`:** a DIO usa Next.js e serializa o catálogo em `<script id="__NEXT_DATA__">`. O scraper lê `props.pageProps.bootcamps`, que traz `slug`, `name`, `type`, `finish` (prazo) e `skills` de cada bootcamp.
2. **Busca genérica no JSON:** se aquela chave sumir, varre o JSON procurando objetos com nome + slug, ignorando subárvores de navegação e i18n.
3. **Parsing HTML:** por último, busca todos os `<a href>` com padrão `/bootcamp/<slug>` e extrai nome, empresa e resumo do contexto ao redor.

> **Por que o caminho canônico vem primeiro.** O mesmo `__NEXT_DATA__` contém `props.pageProps.navigation`, cujos itens também têm `name` e `slug` — mas são **carreiras**, não bootcamps. Seus slugs (`ai-agent-builder`, `ai-automation`…) não correspondem a páginas `/bootcamp/<slug>`, então uma busca genérica casa com eles primeiro e produz um catálogo inteiro de URLs que retornam 404.

Ambas as estratégias são defensivas: campos ausentes recebem valores padrão seguros e erros não interrompem a execução.

---

## Instalação local

```bash
# 1. Clone o repositório
git clone https://github.com/seu-usuario/dio-bootcamp-monitor.git
cd dio-bootcamp-monitor

# 2. Crie e ative o ambiente virtual
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/macOS
source venv/bin/activate

# 3. Instale as dependências
pip install -r requirements.txt
```

---

## Criando o bot no Telegram

1. Abra o Telegram e pesquise por **@BotFather**.
2. Envie `/newbot` e siga as instruções.
3. Escolha um nome e um username para o bot (deve terminar em `bot`).
4. O BotFather entregará um **token** no formato `1234567890:ABCdef...`. Guarde-o.

---

## Obtendo o Chat ID

**Para chat pessoal:**
1. Inicie uma conversa com seu bot (clique em "Iniciar").
2. Abra o Telegram e pesquise por **@userinfobot**.
3. Envie qualquer mensagem. O bot responderá com seu `Id` numérico — esse é o seu `TELEGRAM_CHAT_ID`.

**Para grupos ou canais:**
1. Adicione o bot ao grupo/canal como administrador.
2. Envie uma mensagem no grupo.
3. Acesse: `https://api.telegram.org/bot<SEU_TOKEN>/getUpdates`
4. Procure o campo `"chat": {"id": -100xxxxxxxxxx}`. IDs de grupos/canais são negativos.

---

## Configuração das variáveis de ambiente

Copie o modelo:

```bash
cp .env.example .env
```

Edite `.env` com seus valores reais:

```dotenv
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHI...
TELEGRAM_CHAT_ID=987654321

INITIAL_NOTIFY=false
SEND_DAILY_SUMMARY=false
SEND_EMPTY_SUMMARY=false

MAX_DETAIL_PAGES=10
REQUEST_DELAY_SECONDS=2

LOG_LEVEL=INFO
DIO_BOOTCAMP_URL=https://www.dio.me/bootcamp
```

O `.env` da raiz do projeto é carregado automaticamente por `load_config()`
(parser embutido em `src/config.py`, sem dependência de `python-dotenv`).
Basta preencher o arquivo e rodar `python src/main.py`.

Variáveis já presentes no ambiente **têm precedência** sobre o `.env`. Isso
permite sobrescrever pontualmente:

```bash
# Linux/macOS
MAX_DETAIL_PAGES=50 python src/main.py

# Windows PowerShell
$env:MAX_DETAIL_PAGES = "50"; python src/main.py
```

E é também o que preserva o comportamento no GitHub Actions, onde os secrets
são injetados diretamente no ambiente e não existe `.env`.

> **Atenção:** copie o modelo com `cp` / `Copy-Item`. Renomear o `.env.example`
> deixa o projeto sem o template de referência.

---

## Execução local

```bash
# Executar os testes
pytest -q

# Executar o monitor (lê o .env automaticamente)
python src/main.py

# Com log detalhado
LOG_LEVEL=DEBUG python src/main.py
```

### Receitas de uso

Variáveis de ambiente têm precedência sobre o `.env`, então dá para ajustar
uma execução sem editar arquivo nenhum.

**Primeira execução — registrar o catálogo sem receber 200 mensagens:**

```bash
# Linux/macOS
INITIAL_NOTIFY=false MAX_DETAIL_PAGES=250 python src/main.py

# Windows PowerShell
$env:INITIAL_NOTIFY="false"; $env:MAX_DETAIL_PAGES="250"; python src/main.py
```

Registra e classifica todo o catálogo em silêncio. A partir da execução
seguinte, só chegam bootcamps genuinamente novos. **É o modo recomendado para
começar.**

**Ensaiar sem tocar no histórico nem no Telegram:**

```bash
TELEGRAM_BOT_TOKEN="" TELEGRAM_CHAT_ID="" LOG_LEVEL=DEBUG python src/main.py
```

Sem credenciais o notificador vira *noop*: ele apenas registra no log o que
teria enviado. Útil para conferir a classificação antes de valer.

**Ver o que está aberto agora, sem executar o monitor:**

```bash
python -c "import json; from datetime import date; \
d=json.load(open('data/bootcamps.json',encoding='utf-8')); \
[print(f\"{r['days_left']:4}d  {r['classification']:14} {r['name']}\") \
 for r in sorted([x for x in d if x.get('enrollment_status')=='ABERTO'], \
                 key=lambda r: r['days_left'])]"
```

**Reclassificar tudo em silêncio** (depois de mexer em `src/classifier.py`):

```bash
TELEGRAM_BOT_TOKEN="" TELEGRAM_CHAT_ID="" MAX_DETAIL_PAGES=250 python src/main.py
```

Grava as classificações novas no histórico sem notificar. Sem isso, a próxima
execução interpreta a mudança de critério como "atualização detectada" e
dispara notificações que não correspondem a mudança nenhuma na DIO.

### Quanto tempo leva

O gargalo é `REQUEST_DELAY_SECONDS` (padrão 2s) multiplicado por
`MAX_DETAIL_PAGES`. Varredura completa do catálogo (~217 páginas) leva de 8 a
12 minutos. A execução diária é bem mais rápida: só busca detalhe de bootcamps
novos e dos que ainda estão com inscrição aberta.

---

## GitHub Actions

Com o Actions configurado o bot roda sozinho — seu computador não precisa estar
ligado.

### Configuração dos secrets

Pela interface: **Settings → Secrets and variables → Actions**, criando
`TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`.

Ou pelo [GitHub CLI](https://cli.github.com/), que evita o valor aparecer no
histórico do shell:

```bash
gh secret set TELEGRAM_BOT_TOKEN   # cola o valor no prompt
gh secret set TELEGRAM_CHAT_ID
gh secret list                     # confirma
```

Os secrets ficam protegidos mesmo em repositório público, e o GitHub mascara
automaticamente qualquer valor deles que apareça em log.

### Execução automática

O workflow `.github/workflows/monitor.yml` executa:

- **Diariamente às 08:00 BRT** (11:00 UTC) via `schedule`.
- **Manualmente** via **Actions → Run workflow**, ou:

```bash
gh workflow run monitor.yml -f initial_notify=false -f send_daily_summary=true
gh run list --limit 3      # acompanha
gh run view --log          # lê o log da última execução
```

### O que o workflow faz

1. Faz checkout do repositório.
2. Instala Python 3.12 e dependências.
3. Executa a suíte de testes — **se falhar, o monitor não roda**.
4. Executa o monitor (`src/main.py`), com os secrets vindos do ambiente.
5. Verifica se `data/bootcamps.json` mudou.
6. Se mudou, commita e faz push como `github-actions[bot]`, com `[skip ci]`
   na mensagem para não disparar o workflow de novo.

O `concurrency` impede duas execuções simultâneas.

### Duas coisas para saber

**O commit diário é esperado.** O histórico muda a cada execução, nem que seja
só o `last_checked_at`, então quase sempre há um commit automático — mesmo em
dia sem novidade. Não é sinal de que algo mudou na DIO; para isso, olhe o
Telegram.

**O GitHub desativa cron por inatividade.** Workflows agendados param
automaticamente após 60 dias sem atividade no repositório. Você recebe um
e-mail e reativa com um clique. Os commits automáticos do próprio bot ajudam a
manter o repositório ativo.

Em repositório **público**, o Actions é gratuito e ilimitado nos runners padrão.
Em privado, consome da cota mensal do plano (2.000 min no Free) — a execução
diária leva menos de um minuto, então cabe folgado nos dois casos.

---

## Situação da inscrição (prazo)

O catálogo entrega, para cada bootcamp, um campo `finish` com a data limite —
dado estruturado, não texto. O monitor usa esse prazo como **primeiro filtro**,
antes de qualquer heurística de classificação:

| Situação | Critério | Efeito |
| --- | --- | --- |
| `ABERTO` | prazo ≥ hoje | Classifica normalmente e notifica |
| `ENCERRADO` | prazo < hoje | Vira `EXPIRADA`, não notifica |
| `DESCONHECIDO` | sem prazo ou formato irreconhecível | Não filtra nada; classifica normalmente |

O corte importa mais do que parece: em agosto/2026, **212 dos 217 bootcamps do
catálogo já tinham vencido** — 45 deles ainda de 2021. A DIO mantém os
encerrados publicados como conteúdo de estudo, então sem o filtro por prazo o
bot notifica um arquivo inteiro de programas mortos.

Prazo vencido prevalece sobre a pontuação: não adianta a página falar de
processo seletivo se as inscrições fecharam há três anos.

A notificação mostra a urgência — `🔥` quando faltam 7 dias ou menos, `✅` acima
disso, e o campo é omitido quando não há prazo conhecido.

### Resumo dos abertos

Ao final de cada execução, o bot pode mandar a lista completa do que está com
inscrição aberta, do mais urgente ao menos:

```dotenv
SEND_OPEN_DIGEST=true      # liga o resumo
OPEN_DIGEST_WEEKDAYS=      # vazio = todo dia; "0" = só segunda; "0,4" = segunda e sexta
```

```text
📋 BOOTCAMPS COM INSCRIÇÃO ABERTA (5)

Bootcamp Bradesco - GenAI, Dados & Cyber
🔥 ÚLTIMO DIA (04/08/2026) · BAIXA
https://www.dio.me/bootcamp/bradesco-dados-ciberseguranca-genai

IBM Bob: IA de Nível Empresarial para Desenvolvedores
✅ até 08/09/2026 (35 dias) · MÉDIA
https://www.dio.me/bootcamp/ibm-bob-ia-nivel-empresarial-para-desenvolvedores
```

Diferente das notificações de bootcamp novo, que só chegam quando há novidade,
o resumo é uma **fotografia do estado atual** — útil para não perder prazo de
algo que você já tinha visto e esquecido.

A lista é recalculada na hora a partir da data do catálogo, **não** do
`enrollment_status` gravado: só uma fatia do catálogo é reclassificada por
execução (`MAX_DETAIL_PAGES`), então o valor persistido pode estar velho. A
data, não.

Quando não há nenhum aberto, o resumo é omitido — a menos que
`SEND_EMPTY_SUMMARY=true`.

---

## Interpretação das classificações

| Classificação | Significado |
|---|---|
| **ALTA** | Sinais fortes de processo seletivo ativo: vagas de emprego, candidatos selecionados, entrevistas com a empresa, recrutadores presentes. |
| **MÉDIA** | Sinais moderados: possibilidade real de contratação, recrutadores poderão acessar perfis, conexão direta com RH. |
| **BAIXA** | Menções vagas: Talent Match, certificado para currículo, oportunidades em parceiras sem processo concreto. |
| **EXPIRADA** | Programa antigo ou processo encerrado: inscrições fechadas, edição de ano anterior sem nova seleção. |
| **INDETERMINADA** | Sem evidências suficientes no texto analisado. |

**Atenção:** a classificação analisa contexto, não apenas palavras-chave isoladas.  
Exemplo de falso positivo tratado: *"500 vagas gratuitas para o bootcamp"* → **não** é vaga de emprego.

---

## Como funciona a persistência do JSON

O arquivo `data/bootcamps.json` armazena o histórico completo. Cada entrada contém:

- `stable_id`: identificador derivado do slug da URL (determinístico e estável).
- `name`, `company`, `url`: dados do bootcamp.
- `first_seen_at`, `last_checked_at`: timestamps em ISO 8601 com timezone BRT.
- `classification`, `score`, `evidences`, `observation`: resultado da classificação.
- `notification_status`: `pending` | `sent` | `skipped`.
- `update_notification_hashes`: hashes de atualizações já notificadas (previne duplicidade).
- `catalog_position`, `catalog_summary`, `status`, `launch_info`: dados do catálogo.

**Escrita atômica:** o JSON é escrito em um arquivo temporário no mesmo diretório e depois substituído com `os.replace()`, garantindo que o arquivo nunca fique corrompido a meio caminho.

---

## Comportamento de primeira execução

Na primeira execução (`data/bootcamps.json` vazio ou inexistente):

- **`INITIAL_NOTIFY=false` (padrão):** todos os bootcamps encontrados são registrados com `notification_status=skipped`. Nenhuma notificação é enviada. Nas execuções seguintes, apenas novidades geram alertas.
- **`INITIAL_NOTIFY=true`:** todos os bootcamps encontrados são notificados imediatamente. Use com cautela: o catálogo tem ~217 bootcamps, ou seja, ~217 mensagens em sequência. O notifier respeita um intervalo mínimo de 1,2s entre envios (`MIN_SEND_INTERVAL`), então um baseline completo leva ~4 minutos só de notificação.

> Se for usar `INITIAL_NOTIFY=true`, vale subir `MAX_DETAIL_PAGES` para cobrir o
> catálogo inteiro na mesma execução. Caso contrário, os bootcamps além do limite
> são notificados como `INDETERMINADA`, já que não tiveram a página de detalhe lida.

---

## Limitações conhecidas

1. **Renderização JavaScript:** a DIO usa Next.js com hidratação client-side. Parte do catálogo pode ser carregada via JavaScript após o HTML inicial. O scraper lê o JSON embutido no `__NEXT_DATA__`, no caminho canônico `props.pageProps.bootcamps`; se a chave sumir, cai numa busca genérica no JSON e, por último, no parsing do HTML. Se todos falharem, o catálogo retorna vazio e nenhum dado é alterado.

   > O mesmo `__NEXT_DATA__` traz `props.pageProps.navigation`, cujos itens têm `name` e `slug` mas são **carreiras**, não bootcamps — seus slugs não correspondem a páginas `/bootcamp/<slug>`. Por isso a extração é direcionada ao caminho canônico em vez de varrer o JSON procurando qualquer objeto com nome e slug.

2. **Estrutura HTML mutável:** plataformas modernas modificam classes CSS e estrutura HTML frequentemente. As heurísticas de parsing são defensivas, mas podem precisar de ajuste se a DIO redesenhar o site.

3. **Rate limiting:** o monitor faz poucas requisições e respeita `REQUEST_DELAY_SECONDS`. Ainda assim, em caso de bloqueio temporário (429), ele aguarda com backoff progressivo. Se o bloqueio for permanente (403), encerra sem notificar.

4. **Textos em imagens:** informações de processo seletivo exibidas apenas em imagens (banners, infográficos) não são extraídas — o scraper trabalha apenas com texto.

5. **Páginas de detalhe por execução:** limitado por `MAX_DETAIL_PAGES`. Bootcamps que ultrapassam o limite são classificados apenas pelo resumo do catálogo até a próxima execução.

---

## Como ajustar palavras-chave e pontuação

Edite `src/classifier.py`. Os sinais são definidos em listas de objetos `Signal`:

- **`STRONG_SIGNALS`**: sinais fortes (processo seletivo, vagas de emprego, candidatos selecionados…)
- **`MEDIUM_SIGNALS`**: sinais médios (possibilidade de contratação, recrutadores podem acessar…)
- **`WEAK_SIGNALS`**: sinais fracos (Talent Match, certificado…)
- **`NEGATIVE_SIGNALS`**: indicativos de programa expirado

Cada `Signal` tem:
- `pattern`: expressão regular com `re.IGNORECASE`.
- `points`: pontuação (positiva ou negativa).
- `label`: descrição da evidência.
- `exclude_context_patterns`: padrões que, se presentes no contexto, cancelam o sinal (evita falsos positivos).

**Thresholds de classificação** (em `classify()`):
- Score ≥ 60 → ALTA
- Score ≥ 20 → MÉDIA
- Score ≥ 3 → BAIXA
- Sinais negativos totais ≥ 80 → EXPIRADA
- Demais → INDETERMINADA

---

## Depurando falhas comuns

### Catálogo retorna vazio

```bash
LOG_LEVEL=DEBUG python src/main.py
```

Verifique nos logs se:
- A requisição para `DIO_BOOTCAMP_URL` retornou HTTP 200.
- O `__NEXT_DATA__` foi encontrado.
- Os links `/bootcamp/<slug>` foram detectados no HTML.

Se a DIO mudou a estrutura, abra o HTML salvo e inspecione manualmente:

```python
import requests
r = requests.get("https://www.dio.me/bootcamp", headers={"User-Agent": "Mozilla/5.0"})
print(r.text[:5000])
```

### Notificações não chegam no Telegram

1. Verifique se `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID` estão corretos.
2. Confirme que você iniciou uma conversa com o bot (para chats pessoais).
3. Teste diretamente:

```bash
curl -X POST "https://api.telegram.org/bot<SEU_TOKEN>/sendMessage" \
  -d chat_id=<SEU_CHAT_ID> \
  -d text="Teste de conexão" \
  -d parse_mode=HTML
```

### Testes falhando

```bash
pytest tests/ -v --tb=long
```

Os testes não acessam a internet. Se falharem, verifique se os fixtures em `tests/fixtures/` estão intactos.

### JSON corrompido

O JSON é escrito atomicamente; corrupção não deve ocorrer em condições normais. Se acontecer:

```bash
# Reinicia o histórico (perde dados anteriores)
echo "[]" > data/bootcamps.json
```

Na próxima execução com `INITIAL_NOTIFY=false`, os bootcamps serão registrados sem notificação.
