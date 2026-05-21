# QIS Paper Selection — Knowledge Base

> This file drives paper filtering and scoring. Edit it to refine what gets selected each week.
> Last updated: 2026-05-21

---

## 1. Research Focus Areas

### Tier A — Core (Relevance 4–5)
Topics directly applicable to day-to-day work. Papers here should almost always be selected.

- **Listed equity options**: implied volatility surface, vol dynamics, options pricing models,
  skew, smile, term structure of volatility, variance risk premium, put-call parity,
  options hedging, vol arbitrage, dispersion trading, correlation trading
- **Equity futures**: index futures, basis, roll yield, futures pricing, term structure of futures,
  equity risk premium in futures markets
- **ETF and ETF options**: ETF arbitrage, tracking error, ETF liquidity, creation/redemption,
  ETF options volatility, ETF microstructure
- **Systematic / quantitative equity strategies**: factor models, alpha signal construction,
  momentum, value, quality, low-volatility, carry in equities, signal combination,
  systematic long/short equity, backtesting methodology
- **Volatility trading strategies**: delta hedging, gamma scalping, variance swaps,
  volatility forecasting for trading, realized vs implied vol

### Tier B — High Value (Relevance 3–4)
Strong methodological or applied relevance. Select when no Tier A papers are available.

- **Volatility forecasting**: realized volatility, GARCH, HAR, EGARCH, high-frequency
  volatility estimation, vol-of-vol, VIX forecasting, jump detection
- **Market microstructure**: bid-ask spreads, order flow, price impact, Kyle model,
  informed trading, market making, execution algorithms, transaction costs
- **Portfolio optimization**: mean-variance, risk parity, robust optimization,
  convex optimization with constraints, turnover penalties, factor risk models
- **Machine learning in finance**: return prediction, NLP on earnings/news for alpha,
  gradient boosting / neural nets applied to finance, feature selection for signals
- **Derivatives pricing theory**: stochastic volatility (Heston, SABR), jump-diffusion,
  local vol, rough vol, interest rate derivatives if equity-linked

### Tier C — Medium Value (Relevance 2–3)
Interesting but less directly applicable. Select only if nothing better is available.

- **Macro and rates**: monetary policy, yield curve — only if clearly linked to equity
  derivatives pricing or systematic strategies
- **Risk management**: tail risk, CVaR, stress testing, drawdown control
- **Econometric methods**: new time-series or panel methods applicable to financial data
- **Corporate finance**: earnings quality, accruals, if connected to systematic signals

### Excluded Topics (Relevance 1)
Do NOT select papers primarily about these. Automatically deprioritize.

- Cryptocurrency, DeFi, blockchain (unless pricing listed crypto derivatives)
- Political economy, development economics, labor markets, immigration, climate
- Pure ML / deep learning with no finance application
- Real estate, insurance, health economics
- Social networks, content moderation, NLP unrelated to finance
- Macroeconomics without clear link to equity or derivatives markets

---

## 2. Source Quality Scoring (1–5)

| Score | Sources |
|-------|---------|
| **5** | Journal of Finance (JF), Review of Financial Studies (RFS), Journal of Financial Economics (JFE), Journal of Financial and Quantitative Analysis (JFQA), Review of Asset Pricing Studies (RAPS), Journal of Derivatives |
| **4** | NBER Working Papers, Management Science (finance papers), Journal of Portfolio Management, Mathematical Finance, Quantitative Finance |
| **3** | arXiv q-fin (all subcategories: q-fin.PR, q-fin.TR, q-fin.RM, q-fin.PM, q-fin.MF, q-fin.ST), SSRN working papers |
| **2** | arXiv econ, arXiv cs.* or stat.* with finance application, Semantic Scholar (source unverified) |
| **1** | Blogs, grey literature, unverified preprints, unknown conferences |

**Default scores for current data sources:**
- NBER → **4**
- arXiv with q-fin.* category → **3**
- arXiv with econ.*, cs.*, stat.* category → **2**
- Semantic Scholar (when original journal unknown) → **2**

---

## 3. Relevance Scoring Guide (1–5)

| Score | Criteria |
|-------|----------|
| **5** | Paper is directly about: listed equity options pricing/trading, equity futures, ETF derivatives, or proposes a concrete implementable systematic equity strategy |
| **4** | Closely related: volatility forecasting, equity factor models, options pricing theory, systematic equity strategies, equity market microstructure |
| **3** | Methodologically useful but requires adaptation: ML methods with finance application, general derivatives theory, fixed income with equity-derivatives link |
| **2** | Tangentially related: macro without direct equity focus, general econometrics, corporate finance without clear signal connection |
| **1** | Not relevant to systematic equity derivatives or quantitative strategies |

---

## 4. Keyword Filters

Used for Python pre-filtering before Claude scoring. A paper matching any high-priority
keyword is a candidate. Papers matching only exclusion keywords are dropped.

### High-priority keywords (strong candidate signal)
```
options, implied volatility, volatility surface, vol surface, options pricing,
equity futures, index futures, ETF, exchange-traded fund, systematic strategy,
factor model, factor investing, alpha signal, momentum, value factor, carry,
delta hedging, gamma, vega, variance swap, volatility risk premium,
realized volatility, VIX, CBOE, skew, smile, vol smile, term structure,
put-call, market making, order flow, price impact, bid-ask spread,
portfolio optimization, mean-variance, risk parity, Sharpe ratio,
stochastic volatility, Heston, SABR, local volatility, rough volatility,
dispersion, correlation trading, variance, options strategy
```

### Medium-priority keywords (used as tiebreaker or when high-priority is absent)
```
machine learning, return predictability, cross-sectional returns, time-series momentum,
high-frequency, intraday, microstructure, liquidity, turnover, drawdown,
tail risk, regime, forecasting, GARCH, HAR, jump diffusion, calibration,
systematic, quantitative, backtesting, signal construction, factor premium
```

### Exclusion keywords (automatically reduces relevance score by 1–2)
```
cryptocurrency, bitcoin, ethereum, blockchain, DeFi, NFT, crypto,
immigration, labor market, unemployment, political, climate change,
social media, content moderation, community notes, text classification
```

---

## 5. Final Score Formula

```
Final Score = 0.5 × Relevance (1–5) + 0.5 × Source Quality (1–5)
```

Select the **5 papers with the highest Final Score** from the candidate pool.
In case of ties, prefer more recent papers.

---

## 6. Output Format Per Paper (per PRD)

Each selected paper should include:
1. **Summary**: 1–2 sentences — what the paper does and finds
2. **Key Idea**: 1–2 sentences — the core methodological or conceptual contribution
3. **Relevance Score**: Final score (e.g., 3.5/5) + 1-sentence justification
4. **Potential Application**: 1–2 sentences — how this could be used in practice

---

## 7. Reference Papers (Boss-Approved Exemplars)

These are confirmed high-quality papers that represent exactly the kind of work the team values.
Use them as calibration anchors when scoring relevance.

| Paper | Why it matters |
|-------|----------------|
| Goyal & Saretto (2009) — *Cross-Section of Option Returns and Volatility* (JFE) | IV–HV spread as predictor of option returns; direct signal construction |
| Cao & Han (2013) — *Cross-Section of Option Returns and Idiosyncratic Stock Volatility* (JFE) | Idiosyncratic vol × delta-hedged gains; limits-to-arbitrage angle |
| Xing, Zhang & Zhao (2010) — *What Does the Individual Option Volatility Smirk Tell Us About Future Equity Returns?* (JFQA) | Smirk slope as return predictor; informed-trading vs demand-pressure debate |
| Driessen, Maenhout & Vilkov (2009) — *The Price of Correlation Risk* (JF) | Correlation risk premium; dispersion trading decomposition |

**What these papers share:** They treat options as an asset class with its own cross-sectional return structure, not just as hedging instruments. Papers with this angle should be scored **4–5** even if they are primarily empirical with limited new theory.

---

## 8. Feedback Log

> Updated: 2026-05-21

**Boss feedback (2026-05-21):**
- Papers about **options cross-section**, **vol smirk/skew as return predictor**, and **correlation risk premium** are directly relevant and should score 4–5.
- Papers about **variance swaps**, **dispersion trading**, and **volatility risk premium decomposition** are core Tier A topics.
- The team is also interested in catching up on classic literature: equity vol, var swaps, equity futures, systematic trading strategies, ETFs.
- A one-off **Special Issue** of 10–20 well-cited classics (1990–present) has been requested in addition to the normal weekly digest.
