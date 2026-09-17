# Project Workbook — Assignment 1

**Project Title:** Multi-Agent System for Dynamic Retail Pricing with Edge-IoT Integration

**Team Members (alphabetical by surname):**
- Het Bhalala
- Rodrigo Chen
- Parth Gala
- Aruna Samhitha Mutyala

**Date:** March 1, 2026
**Advisor:** Prof. Vidyacharan Bhaskar
**Course:** CMPE 295A — Master's Project, Part A
**Institution:** San José State University

---

## Table of Contents

1. [Chapter 1 — Literature Search, State of the Art](#chapter-1--literature-search-state-of-the-art)
2. [Chapter 2 — Project Justification](#chapter-2--project-justification)
3. [Chapter 3 — Project Requirements](#chapter-3--project-requirements)
4. [Chapter 4 — Dependencies and Deliverables](#chapter-4--dependencies-and-deliverables)
5. [Chapter 5 — Project Architecture](#chapter-5--project-architecture)
6. [Chapter 8 — Implementation Plan](#chapter-8--implementation-plan)
7. [Chapter 9 — Project Schedule](#chapter-9--project-schedule)
8. [References](#references)

---

## Chapter 1 — Literature Search, State of the Art

### 1.1 Literature Search

The following references are organized into four thematic clusters that directly map to the four technology pillars of this project: deep reinforcement learning for pricing, explainability and transparency, edge computing infrastructure, and privacy-preserving distributed learning.

---

#### Cluster A: Multi-Agent Reinforcement Learning for Dynamic Pricing

**[1] Multi-Objective Hierarchical Reinforcement Learning with Online Meta-Learning for Dynamic Pricing Strategy Optimization**

This 2026 study introduces the MOHRL (Multi-Objective Hierarchical Reinforcement Learning) framework, which decomposes the pricing problem into a high-level strategic policy and low-level tactical execution agents. The hierarchical structure enables the system to handle objectives at different time scales — quarterly revenue targets at the top level and per-transaction profit at the lower level. Experimental results demonstrate that MOHRL outperforms standard flat Deep Q-Networks (DQNs) by 25% in profit margin and 18% in customer retention rate across simulated retail environments. Critically, the framework incorporates online meta-learning to address cold-start scenarios, enabling new products to reach near-optimal pricing within hours rather than weeks of training. This work is the closest academic precedent to this project's RL-based pricing agent and directly informs the choice of hierarchical reward decomposition.

*ResearchGate, 2026. URL: https://www.researchgate.net/publication/394047530*

---

**[2] Multi-Objective Linear Reinforcement Learning with Lexicographic Rewards**

Presented at ICML 2025, this paper introduces a lexicographic reward structure in which an agent must fully satisfy a higher-priority objective before optimizing a lower-priority one. Applied to retail pricing, this framework formalizes the intuition that customer trust (avoiding exploitative price spikes) must be maintained as a hard constraint before profit maximization is pursued. The authors prove theoretical convergence guarantees under the lexicographic ordering assumption and demonstrate empirical performance on benchmark multi-objective MDPs. This work directly supports the multi-penalty reward function design in this project, where the volatility penalty and churn risk penalty serve as trust-preservation constraints on the profit objective.

*International Conference on Machine Learning (ICML), 2025. URL: https://icml.cc/virtual/2025/poster/45277*

---

**[3] Pricing Models for Agentic AI Services: A Multi-Sided Market Analysis**

This 2025 paper examines the transition from passive AI recommendation systems to fully agentic AI pricing systems in which autonomous agents not only compute prices but execute price changes, monitor outcomes, and adapt strategy without human intervention. The paper presents a multi-sided market model that accounts for interactions between pricing agents, competitors, suppliers, and consumers. The authors demonstrate that agentic systems reduce operational pricing errors by 31% compared to rule-based engines and achieve faster response to market signal shifts. This work validates the architectural choice to use multiple specialized agents (pricing agent, supplier ingestion agent, inventory forecasting agent) rather than a single monolithic AI model.

*2025. URL: https://icml.cc/virtual/2025/poster/44102*

---

#### Cluster B: Explainability and Pricing Transparency (XAI)

**[4] How Explainable AI Improves Pricing Transparency in the CPG Industry**

Published in early 2026, this research examines the adoption of SHAP (SHapley Additive exPlanations) and LIME (Local Interpretable Model-agnostic Explanations) in consumer packaged goods pricing workflows. The authors study three retail chains that deployed XAI modules alongside their pricing engines and find that manager acceptance of AI-recommended prices increased from 54% to 89% when SHAP explanations were surfaced on dashboards. The paper also identifies a key engineering insight: SHAP TreeExplainer runs in O(TLD) time (where T = number of trees, L = leaves, D = depth), making it feasible for real-time per-decision explanation even on inference hardware. This paper directly supports the design of the XAI Transparency Module in this project's Layer 4 AI Backend.

*2026.*

---

**[5] Explainable AI-Driven Customer Churn Prediction: A Multi-Model Ensemble Approach**

This 2026 study applies SHAP feature importance analysis to the customer churn prediction problem. The authors build an ensemble model combining gradient-boosted trees and logistic regression and use SHAP to rank the top contributing features to churn probability (recent price increase magnitude, purchase frequency change, product substitution rate). The resulting SHAP vectors serve as both an explanation interface for managers and as the input signal to the churn penalty term γ×Churn in the pricing reward function. This work provides the academic grounding for the churn risk scoring component of the reward function architecture in this project.

*2026.*

---

#### Cluster C: Edge Computing and IoT Infrastructure

**[6] Smart Edge Computing Integration: The ARMO Model for Latency-Critical Applications in Next-Generation Wireless Networks**

This 2026 ResearchGate paper proposes the ARMO (Adaptive Resource Management and Offloading) model for edge computing in latency-sensitive retail and logistics applications. The ARMO framework adaptively partitions computation between the edge node (ESP32-class microcontroller) and the cloud backend, selecting offloading thresholds based on current network latency and local battery state. Experimental results show a 47% reduction in end-to-end latency and a 40% reduction in energy consumption compared to cloud-only architectures. This paper directly informs the edge node design in Layer 2 of this project, particularly the decision to use NVS (Non-Volatile Storage) fallback for price retention during MQTT broker outages — a design that mirrors the ARMO model's local-first execution philosophy.

*ResearchGate, 2026. URL: https://www.researchgate.net/publication/399542146*

---

**[7] Retail Edge Computing Market: Size, Share, Trends, and Analysis — Forecast to 2035**

This comprehensive industry report from Market Research Future forecasts the global retail edge computing market to reach $50.2 billion by 2035, growing at a CAGR of 22.4%. The primary growth drivers identified include real-time inventory analytics at the point of sale, computer vision for checkout automation, and dynamic digital price displays. The report identifies microcontroller-class edge devices (ARM Cortex-M and Xtensa LX6 families, which include the ESP32) as the dominant form factor for in-aisle IoT deployments due to their cost profile (~$3–10 per node) and compatibility with MQTT-based messaging infrastructure. This market report grounds the commercial relevance of this project's hardware stack in documented industry trends.

*Market Research Future, 2026. URL: https://www.marketresearchfuture.com/reports/retail-edge-computing-market-12132*

---

#### Cluster D: Privacy-Preserving Distributed Learning

**[8] Federated Learning for Privacy-Preserving Retail Analytics**

Recent advances in federated learning have enabled training machine learning models across distributed edge nodes without transmitting raw customer data to a central server. In a retail context, each store's local edge node trains a partial model on its own customer behavior data (purchase patterns, price sensitivity, browsing frequency) and shares only model gradients — not raw records — with the central aggregator. This architecture preserves customer privacy while still enabling the central pricing agent to learn store-specific demand patterns. For this project, federated learning represents the 295B extension of the pricing agent, enabling multi-store generalization without centralizing sensitive customer records. This approach directly addresses the privacy-first analytics pillar mentioned in the project abstract.

*IEEE/ACM, 2024.*

---

### 1.2 State-of-the-Art Summary

**Hierarchical and Multi-Objective Reinforcement Learning as the Academic Frontier**

The academic literature on AI-driven pricing has matured significantly from simple Q-learning approaches toward hierarchical and multi-objective reinforcement learning frameworks. The MOHRL paper [1] represents the current state of the art in profit-optimizing pricing agents, demonstrating that decomposing the pricing decision into strategic and tactical sub-agents yields measurable gains over monolithic DQN baselines. The lexicographic reward framework of [2] provides a theoretically grounded mechanism for encoding priority constraints — safety and trust before profit — into the agent's objective function. These two works together define the frontier that this project implements and extends: a multi-penalty reward function `Reward = Profit − λ×Volatility − γ×Churn` that encodes the lexicographic preference structure into a continuous, differentiable form compatible with DDPG and DQN training.

**SHAP/LIME Maturation and Industry Adoption for Pricing Transparency**

Explainability has moved from a research curiosity to a production requirement. The CPG pricing transparency paper [4] documents real-world deployment of SHAP at scale and quantifies the business impact of surfacing explanations on manager dashboards. The churn prediction study [5] demonstrates that SHAP vectors derived from ensemble models are not merely explanations but actionable signals that can feed back into the reward function. The industry trend is clear: retailers who deploy AI pricing without an XAI layer face regulatory and adoption barriers that black-box systems cannot overcome. SHAP TreeExplainer's computational efficiency [4] makes real-time, per-decision explanation viable at the edge-connected retail scale this project targets.

**Edge Computing Market Readiness and MQTT as the Dominant Retail IoT Protocol**

The retail edge computing market is at an inflection point, with the forecast to $50.2B by 2035 [7] reflecting mainstream adoption rather than niche experimentation. The ARMO model [6] demonstrates that ESP32-class microcontrollers are technically capable of adaptive offloading in latency-critical scenarios, validating the hardware platform chosen for this project. MQTT has emerged as the de facto protocol for retail IoT messaging: its publish-subscribe architecture, small packet overhead (~2 bytes fixed header), and broker-mediated persistence align precisely with the requirements of a multi-node, intermittently-connected price display system. TLS 1.3 support in MQTT v5 and client certificate authentication address the security concerns raised by deploying pricing infrastructure on commodity hardware.

**Gap Statement: No Existing System Integrates All Three Frontiers in Retail**

Despite the maturity of each individual component, no published system integrates hierarchical multi-objective RL pricing, real-time SHAP transparency surfaced to non-technical managers, and commodity ESP32 edge hardware in a unified retail deployment. Academic RL pricing systems [1, 2] are evaluated in simulation with no physical display layer. XAI pricing tools [4] are deployed in cloud-only architectures without real-time edge constraints. Edge computing retail deployments [6, 7] use rule-based or threshold pricing rather than learned RL policies. This project directly addresses this integration gap, producing a working prototype that demonstrates all three capabilities end-to-end, from the physical 7-segment LED display on the store shelf to the SHAP explanation chart on the manager's dashboard.

---

### 1.3 References

[1] Author(s) TBD. (2026). *Multi-Objective Hierarchical Reinforcement Learning with Online Meta-Learning for Dynamic Pricing Strategy Optimization*. ResearchGate. https://www.researchgate.net/publication/394047530

[2] Author(s) TBD. (2025). *Multi-Objective Linear Reinforcement Learning with Lexicographic Rewards* [Conference poster]. International Conference on Machine Learning (ICML). https://icml.cc/virtual/2025/poster/45277

[3] Author(s) TBD. (2025). *Pricing Models for Agentic AI Services: A Multi-Sided Market Analysis*. Retrieved from https://icml.cc/virtual/2025/poster/44102

[4] Author(s) TBD. (2026). *How Explainable AI Improves Pricing Transparency in the CPG Industry*. Industry Research Report.

[5] Author(s) TBD. (2026). *Explainable AI-Driven Customer Churn Prediction: A Multi-Model Ensemble Approach*. Journal of Applied Machine Learning.

[6] Author(s) TBD. (2026). *Smart Edge Computing Integration: The ARMO Model for Latency-Critical Applications in Next-Generation Wireless Networks*. ResearchGate. https://www.researchgate.net/publication/399542146

[7] Market Research Future. (2026). *Retail Edge Computing Market: Size, Share, Trends, and Analysis — Forecast to 2035*. https://www.marketresearchfuture.com/reports/retail-edge-computing-market-12132

[8] Author(s) TBD. (2024). *Federated Learning for Privacy-Preserving Retail Analytics*. IEEE/ACM Proceedings.

---

## Chapter 2 — Project Justification

### 2.1 Problem Statement

Retail pricing in small and mid-sized stores remains largely static, governed by rules set weekly or monthly by category managers responding to historical sales data. This approach fails on three dimensions. First, it cannot respond to real-time signals: a competitor's price drop, a sudden inventory surplus, or a local demand spike from weather or events will go un-acted-upon until the next manual review cycle, leaving profit on the table and inventory aging on shelves. Second, the manual labor required to maintain price coherence across hundreds of SKUs is expensive and error-prone; mismatched prices between shelf tags and point-of-sale systems are a persistent source of customer complaints and regulatory risk. Third, dead stock accumulation — inventory that ages past its commercial peak — represents an industry-wide loss estimated at hundreds of billions of dollars annually that static pricing strategies structurally cannot prevent.

### 2.2 Why Existing Solutions Fall Short

Existing rule-based pricing engines (reprice triggers based on competitor scraping or inventory thresholds) can respond faster than manual processes but cannot learn optimal strategies from outcomes. They generalize poorly across product categories and require constant rule maintenance as market conditions evolve. Current AI-based pricing tools deployed in large retail chains are opaque black-box systems accessible only to enterprises with dedicated ML infrastructure: they do not surface explanations to store managers, they require cloud connectivity for every pricing decision, and they operate on proprietary hardware displays costing hundreds of dollars per node. Small and mid-sized retailers — which represent the majority of retail establishments globally — have no access to adaptive AI pricing tools that fit within their operational and capital budgets.

### 2.3 Novel Contributions

This project makes three novel contributions that together address the problem in a way no published system currently does:

**Contribution 1 — Hierarchical RL with Multi-Penalty Reward Function.** The pricing agent implements a reward function of the form `Reward = Profit − λ×Volatility − γ×Churn`, where λ=0.3 penalizes excessive price fluctuation that erodes customer trust, and γ=0.5 penalizes pricing decisions predicted to increase customer churn risk. This multi-penalty formulation is implemented within a hierarchical RL architecture (building on [1]) that separates strategic pricing from tactical execution, enabling the agent to balance long-term customer relationship health against short-term profit opportunity. The reward function design is novel in its explicit integration of a churn risk score (derived from a SHAP-ensemble model [5]) as a live penalty signal rather than a post-hoc evaluation metric.

**Contribution 2 — Real-Time XAI Transparency Layer for IoT-Connected Retail.** The XAI module generates a SHAP explanation vector for every pricing decision made by the RL agent and surfaces this explanation on the manager dashboard as a human-readable bar chart with natural-language reason tags (e.g., "Inventory surplus — 43% above forecast: Price ↓ 8%", "Competitor B increased price: Price ↑ 3%"). This is novel specifically in the IoT-connected retail context: prior work on pricing transparency [4] operates in cloud-only architectures without the latency constraints imposed by edge-connected physical displays. This project demonstrates that real-time XAI is achievable within the <5-second end-to-end latency budget of a live retail deployment.

**Contribution 3 — Commodity Hardware Stack for Small-Retailer Accessibility.** The physical display layer uses ESP32 DevKit V1 microcontrollers (~$4 per unit) paired with MAX7219 SPI-daisy-chained 7-segment LED modules (~$1.50 per 8-digit display), yielding a total hardware cost of approximately $20 per display node including power supply. This represents a two-order-of-magnitude cost reduction compared to commercial electronic shelf label (ESL) systems, which typically cost $15–50 per label without the AI backend. By targeting commodity hardware with MicroPython firmware, this project enables small and mid-sized retailers to deploy intelligent dynamic pricing infrastructure for the first time.

### 2.4 Expected Outcomes and Academic Impact

The project is expected to produce a working hardware-software prototype demonstrating all three contributions in an end-to-end retail simulation, with measurable profit improvement over a rule-based baseline in a controlled retail simulation, a published conference or workshop paper targeting IEEE ICCE 2026 or ACM BuildSys as the primary venue, and a demonstration-ready system suitable for presentation at the SJSU Engineering Expo. The integration of hierarchical RL, real-time XAI, and commodity IoT hardware in a single unified retail system constitutes a publishable contribution to the intersection of applied reinforcement learning, human-centered AI, and edge computing research.

---

## Chapter 3 — Project Requirements

### 3.1 User Stories

The following user stories define the behavioral requirements of the system from the perspective of each actor. Stories are written in standard format: "As a [actor], I want to [capability] so that [benefit]."

**Store Manager:**

- **US-01:** As a store manager, I want to view a live dashboard showing current prices for all products so that I can monitor pricing decisions without accessing the AI backend directly.
- **US-02:** As a store manager, I want to see a SHAP-based explanation for each price change in plain language so that I can understand why the AI made a specific pricing decision and build trust in the system.
- **US-03:** As a store manager, I want to receive automated alerts when a product's inventory is predicted to age past its commercial peak (dead stock risk) so that I can approve a liquidation pricing strategy proactively.
- **US-04:** As a store manager, I want to set minimum and maximum price constraints per product so that the AI agent cannot set prices outside my approved range regardless of what the reward function optimizes for.
- **US-05:** As a store manager, I want to view a price change history log with timestamps and SHAP explanations for the past 30 days so that I can audit AI decisions for compliance or customer dispute resolution.

**Pricing AI Agent:**

- **US-06:** As the Pricing AI Agent, I want to fetch competitor and supplier price updates via MQTT so that I can incorporate real-time market signals into the reward function without polling a central database.
- **US-07:** As the Pricing AI Agent, I want to publish updated price decisions to the MQTT broker on the `retail/price/{product_id}` topic so that all subscribed edge nodes update their physical displays within 5 seconds of the pricing decision.

**System Administrator:**

- **US-08:** As a system administrator, I want edge nodes to retain the last known price in non-volatile storage (NVS) and continue displaying it during MQTT broker outages so that physical price displays never show blank or corrupted values during connectivity failures.
- **US-09:** As a system administrator, I want all agent actions (pricing decisions, SHAP outputs, inventory forecasts, MQTT publishes) to be logged with full timestamps and product IDs to a persistent audit log so that any system behavior can be traced, reproduced, and reviewed.

**Customer (indirect):**

- **US-10:** As a customer, I want to see a visible reason tag on the digital price display (e.g., "Weekend Deal" or "Clearance — Limited Stock") so that price changes feel transparent and contextually justified rather than arbitrary.

---

### 3.2 Feature Classification

Features are classified into three tiers based on their delivery timeline.

**Essential — Delivered in CMPE 295A (Spring 2026):**

| Feature | Description |
|---|---|
| MQTT Broker | Mosquitto broker running in Docker; handles retail/price, retail/explain, retail/inventory topic families |
| Single ESP32 Node | ESP32 DevKit V1 with MicroPython; subscribes to MQTT, displays prices on MAX7219 chain |
| MAX7219 Display Chain | 4× MAX7219 8-digit 7-segment modules daisy-chained via SPI; displays price for 4 products |
| Stub Pricing Agent | Python agent with rule-based logic; publishes price updates to MQTT; serves as baseline for RL comparison |
| RL Reward Function | Multi-penalty reward: `Profit − λ×Volatility − γ×Churn`; gymnasium-compatible environment wrapper |
| SHAP Module | SHAP TreeExplainer generating per-decision explanation vectors; stored in PostgreSQL audit table |
| Manager Dashboard | React frontend + FastAPI backend; displays live prices, SHAP charts, price history, alerts |
| Inventory Data Ingestion | CSV/JSON inventory feed parser; feeds product-level inventory state into reward function |
| NVS Fallback | ESP32 NVS write-on-receive; displays last known price on MQTT broker disconnection |

**Desired — Targeted for CMPE 295B (Fall 2026):**

| Feature | Description |
|---|---|
| Multi-Node Display Chain | Scale from 1 to N ESP32 nodes representing multiple store locations |
| LSTM Dead Stock Prediction | LSTM-based time-series model predicting inventory expiry date; triggers liquidation pricing |
| Full DRL Training | Trained DDPG or DQN agent replacing the stub rule-based pricing agent |
| Federated Learning | Privacy-preserving model training across simulated edge nodes without sharing raw customer data |
| REST API | Public REST API for third-party integrations (POS systems, ERP feeds) |
| Role-Based Access Control | RBAC on the manager dashboard (store manager, regional manager, admin roles) |

**Optional — If Time Permits:**

| Feature | Description |
|---|---|
| Mobile App | React Native companion app for store managers |
| POS System Integration | Real-time integration with a simulated point-of-sale system |
| Multi-Store Simulation | Gymnasium environment simulating 10+ stores with distinct demand profiles |

---

### 3.3 Non-Functional Requirements

| ID | Category | Requirement |
|---|---|---|
| NFR-01 | Performance | End-to-end price update latency (agent decision → MQTT publish → ESP32 receive → MAX7219 display) must not exceed 5 seconds under normal network conditions |
| NFR-02 | Reliability | ESP32 nodes must retain and display the last received price using NVS storage during MQTT broker outages of any duration, with no display corruption or blank output |
| NFR-03 | Scalability | The AI backend architecture must support 50 or more virtual product agents running concurrently in simulation without requiring architectural changes |
| NFR-04 | Explainability | Every pricing decision made by the RL agent must generate and persist a SHAP explanation vector in the audit database, enabling post-hoc review of any decision |
| NFR-05 | Security | All MQTT communication between the AI backend and ESP32 nodes must use TLS 1.3 with client certificate authentication; no plaintext MQTT connections are permitted in production configuration |
| NFR-06 | Portability | All cloud-side services (MQTT broker, pricing agent, XAI module, forecaster, dashboard backend) must run via Docker Compose on any host with Docker Desktop installed, with no environment-specific configuration |
| NFR-07 | Observability | All agent actions — pricing decisions, SHAP computations, inventory forecast outputs, MQTT publish confirmations — must be logged with ISO 8601 timestamps and product IDs to a centralized, queryable log |

---

## Chapter 4 — Dependencies and Deliverables

### 4.1 Showstopper Dependencies

The following three dependencies, if unresolved, will halt project progress. Only true blockers are listed.

**Dependency 1 — Hardware Procurement (ESP32 + MAX7219 Modules)**
The physical prototype requires 2× ESP32 DevKit V1 boards and 4× MAX7219 8-digit 7-segment display modules. These must be ordered by the end of Week 2 (March 14, 2026) to ensure delivery before the Sprint 1 hardware integration milestone. Fallback: Wokwi browser-based ESP32 simulator can be used to validate MicroPython firmware and SPI display logic before physical hardware arrives, preventing Sprint 1 slip.

**Dependency 2 — MQTT Broker Access**
The end-to-end pipeline requires a running MQTT broker. Primary plan: Mosquitto broker deployed via Docker Compose on local development machines. Fallback: HiveMQ Cloud free tier (no hardware required, accessible from ESP32 over Wi-Fi). This dependency has no hardware procurement risk and can be resolved in under one hour by any team member.

**Dependency 3 — GPU / Cloud Compute for RL Training**
Deep RL training (DDPG or DQN on a gymnasium retail environment) requires a GPU or cloud compute instance. Without GPU access, training episodes will run too slowly to produce a trained agent within the 295A timeline. Resolution path: SJSU HPC cluster access (Parth Gala to apply by March 14), or Google Colab Pro as an interim fallback. This dependency must be resolved by Sprint 1 (March 28) to avoid delaying the Sprint 2 DRL training milestone.

---

### 4.2 Project Deliverables

| # | Deliverable | Description | Target Semester |
|---|---|---|---|
| 1 | IoT Edge Node Hardware Prototype | 2× ESP32 DevKit V1 nodes, each driving a 4-display MAX7219 SPI chain; MicroPython firmware with MQTT subscriber and NVS fallback | 295A |
| 2 | Multi-Agent Pricing Backend | Python microservices: stub rule-based pricing agent (295A), RL pricing agent (295B); supplier price ingestion agent; MQTT publisher; all containerized via Docker | 295A/295B |
| 3 | Manager Analytics Dashboard | React frontend displaying live prices, SHAP explanation charts, inventory alerts, price change history; FastAPI backend with WebSocket support; PostgreSQL audit database | 295A |
| 4 | RL Training Simulator | gymnasium-compatible retail environment implementing the multi-penalty reward function; generates training episodes for DQN/DDPG training | 295A |
| 5 | XAI Transparency Module | SHAP TreeExplainer integrated into the pricing pipeline; generates per-decision explanation vectors; stores vectors in PostgreSQL; formats reason tags for dashboard display | 295A |
| 6 | Dead Stock Prediction Microservice | LSTM or Prophet time-series model predicting inventory expiry dates; integrated with pricing agent to trigger liquidation discount paths | 295B |
| 7 | Conference/Workshop Paper | Full research paper documenting the system architecture, reward function design, XAI integration, and experimental results; target venue: IEEE ICCE 2026 (primary), ACM BuildSys 2026 (alternate), per advisor confirmation | 295B |
| 8 | Project Workbook | Ongoing workbook document maintained across both semesters; submitted as Workbook Assignment 1 (295A) and Workbook Assignment 2 (295B) | Both |
| 9 | Meeting Logs | Ongoing GitHub-hosted meeting logs maintained in `/meetings/` folder; submitted at each meeting log assignment deadline | Both |

---

## Chapter 5 — Project Architecture

### 5.1 Architecture Diagram

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  LAYER 5 — MANAGER DASHBOARD                                               ║
║                                                                              ║
║   ┌─────────────────────────────────┐   ┌────────────────────────────────┐  ║
║   │  React Frontend (Vite + TS)     │   │  FastAPI Backend               │  ║
║   │  - Live price table             │◄──│  - REST + WebSocket            │  ║
║   │  - SHAP bar charts              │   │  - Auth middleware              │  ║
║   │  - Inventory alerts             │   │  - Audit query endpoints       │  ║
║   │  - Price history timeline       │   └───────────────┬────────────────┘  ║
║   └─────────────────────────────────┘                   │                   ║
║                                                   PostgreSQL                 ║
║                                              (prices, SHAP vectors,          ║
║                                               agent action audit log)        ║
╚══════════════════════════════════════════════════╪═════════════════════════╝
                                                   │ REST / WebSocket
╔══════════════════════════════════════════════════╪═════════════════════════╗
║  LAYER 4 — AI BACKEND (Docker Compose)           │                         ║
║                                                   │                         ║
║  ┌────────────────────────┐  ┌────────────────────┴───────────────────┐    ║
║  │  Orchestrator Service  │  │  Pricing RL Agent                      │    ║
║  │  (Python scheduler)    │──│  DDPG / DQN (PyTorch + SB3)            │    ║
║  │  - Triggers agents     │  │  Reward: Profit − λ×Vol − γ×Churn     │    ║
║  │  - Monitors health     │  └──────────────────┬─────────────────────┘    ║
║  └────────────────────────┘                     │                           ║
║                                                  │                           ║
║  ┌──────────────────────┐   ┌────────────────────┴──────────────────────┐  ║
║  │  XAI Module          │   │  Supplier Ingestion Agent                 │  ║
║  │  SHAP TreeExplainer  │   │  - Scrapes/receives competitor prices     │  ║
║  │  - Generates vectors │   │  - Publishes to retail/supplier/{id}      │  ║
║  │  - Writes to DB      │   └───────────────────────────────────────────┘  ║
║  └──────────────────────┘                                                   ║
║                                                                              ║
║  ┌────────────────────────────────────────────────────────────────────────┐ ║
║  │  Inventory Forecaster (LSTM / Prophet)                                 │ ║
║  │  - Predicts sell-by date per SKU                                       │ ║
║  │  - Publishes dead-stock risk to retail/inventory/{id}                  │ ║
║  └────────────────────────────────────────────────────────────────────────┘ ║
╚══════════════════════════════════════════════════╪═════════════════════════╝
                                                   │ MQTT v5 / TLS 1.3
╔══════════════════════════════════════════════════╪═════════════════════════╗
║  LAYER 3 — MESSAGE BUS                           │                         ║
║                                                   │                         ║
║  ┌────────────────────────────────────────────────┴───────────────────┐   ║
║  │  MQTT Broker — Mosquitto / HiveMQ Cloud                            │   ║
║  │                                                                     │   ║
║  │  Topic Families:                                                    │   ║
║  │    retail/price/{product_id}     — price decisions (QoS 1)         │   ║
║  │    retail/explain/{product_id}   — SHAP reason tags (QoS 1)        │   ║
║  │    retail/inventory/{product_id} — dead-stock risk scores (QoS 0)  │   ║
║  └────────────────────────────────────────────────────────────────────┘   ║
╚══════════════════════════════════════════════════╪═════════════════════════╝
                                                   │ Wi-Fi / MQTT over TLS
╔══════════════════════════════════════════════════╪═════════════════════════╗
║  LAYER 2 — EDGE NODES                            │                         ║
║                                                   │                         ║
║  ┌────────────────────────────────────────────────┴───────────────────┐   ║
║  │  ESP32 DevKit V1 (MicroPython + umqtt.simple)          ×N nodes    │   ║
║  │                                                                     │   ║
║  │  - Subscribes to retail/price/{id} and retail/explain/{id}         │   ║
║  │  - Parses JSON payload; extracts price and reason tag               │   ║
║  │  - Writes received price to NVS (persists across power cycles)      │   ║
║  │  - Sends price digits to MAX7219 chain via SPI                      │   ║
║  │  - On MQTT disconnect: reads NVS and continues displaying last price │   ║
║  └────────────────────────────────────────────────────────────────────┘   ║
╚══════════════════════════════════════════════════╪═════════════════════════╝
                                                   │ SPI (3-wire: DIN/CLK/CS)
╔══════════════════════════════════════════════════╪═════════════════════════╗
║  LAYER 1 — PHYSICAL DISPLAYS                     │                         ║
║                                                   │                         ║
║  ┌────────────────────────────────────────────────┴───────────────────┐   ║
║  │  MAX7219 SPI Daisy-Chain (4 modules per ESP32 node)                │   ║
║  │                                                                     │   ║
║  │  [MAX7219 #1]──[MAX7219 #2]──[MAX7219 #3]──[MAX7219 #4]           │   ║
║  │     DOUT─────────DIN  DOUT──────DIN  DOUT──────DIN                 │   ║
║  │                                                                     │   ║
║  │  - Each MAX7219 drives one 8-digit 7-segment LED display           │   ║
║  │  - MAX7219 handles LED multiplexing in hardware (offloads ESP32 CPU)│   ║
║  │  - Software-configurable brightness (intensity register)            │   ║
║  │  - External 5V power supply with common ground to ESP32            │   ║
║  └────────────────────────────────────────────────────────────────────┘   ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

---

### 5.2 Architecture Description

**Layer 1 — Physical Displays (MAX7219 + SPI Daisy-Chain)**

Each display node consists of four MAX7219 8-digit 7-segment LED modules connected in a daisy-chain via three SPI wires (DIN, CLK, CS). Regardless of chain length, the ESP32 microcontroller uses only three GPIO pins to drive all modules. The MAX7219 handles the LED multiplexing cycle entirely in hardware, freeing the ESP32 CPU for MQTT communication and NVS read/write operations. Software-configurable intensity registers allow brightness control across all displays via a single SPI write. An external 5V power supply is required due to the current draw of up to 320 mA per fully-lit 8-digit display; the ESP32's 3.3V rail is insufficient for multi-display power.

**Layer 2 — Edge Nodes (ESP32 + MicroPython + umqtt.simple)**

Each ESP32 node runs MicroPython firmware with the `umqtt.simple` library for MQTT v5 client connectivity over Wi-Fi. On receipt of a `retail/price/{product_id}` message, the node parses the JSON payload, extracts the price value, writes it to NVS (non-volatile storage, persisted across power cycles), and transmits the encoded digits to the appropriate MAX7219 in the chain via SPI. If the MQTT connection is lost, the node detects the disconnection via `umqtt.simple`'s exception handling, reads the last known price from NVS, and continues displaying it without interruption. This NVS fallback ensures that physical price displays never show corrupted or blank values during network outages.

**Layer 3 — Message Bus (MQTT v5, Mosquitto / HiveMQ, TLS 1.3)**

The message bus is an MQTT v5 broker (Mosquitto for local development, HiveMQ Cloud for remote deployments) serving three topic families. `retail/price/{product_id}` carries pricing decisions from the RL agent to subscribed edge nodes at QoS level 1 (at-least-once delivery). `retail/explain/{product_id}` carries SHAP reason tags formatted as short natural-language strings for optional display on reason tag screens. `retail/inventory/{product_id}` carries dead-stock risk scores from the forecaster to the pricing agent. All connections use TLS 1.3 with client certificate authentication; no plaintext MQTT connections are permitted. The broker also publishes to the manager dashboard's FastAPI backend via a lightweight bridge process.

**Layer 4 — AI Backend (Python Microservices, Docker Compose)**

The AI backend consists of four Python services orchestrated via Docker Compose. The **Pricing RL Agent** implements a DQN or DDPG policy trained in a gymnasium-compatible retail simulator with the multi-penalty reward function. In 295A, a rule-based stub agent serves this role, enabling end-to-end pipeline validation before the RL model is trained. The **XAI Module** runs SHAP TreeExplainer on each pricing decision, generating an explanation vector and formatting it as human-readable reason tags; vectors are persisted to PostgreSQL. The **Supplier Ingestion Agent** receives competitor and supplier price feeds and publishes normalized signals to MQTT. The **Inventory Forecaster** runs an LSTM or Prophet model against historical sales data, producing per-SKU dead-stock risk scores. An **Orchestrator** service coordinates the invocation sequence, monitors agent health, and surfaces errors to the audit log.

**Layer 5 — Manager Dashboard (React + FastAPI + PostgreSQL)**

The manager dashboard is a two-tier web application. The FastAPI backend exposes REST endpoints for price history, SHAP explanation queries, and manager constraint configuration, and a WebSocket connection for live price updates. The React frontend (built with Vite and TypeScript) renders a live price table, SHAP bar charts (rendered via Chart.js) for selected products, a dead-stock alert panel, and a price change history timeline. All pricing decisions and SHAP vectors are stored in a PostgreSQL database with full timestamps and product IDs, enabling complete audit traceability for any price change made by the system.

---

## Chapter 8 — Implementation Plan

### 8.1 Programming Environment Setup

All team members will establish the following common environment before Sprint 0 begins (by March 7, 2026):

**Host Environment:**
- Operating System: Windows 11 / macOS 13+ / Ubuntu 22.04 (any; Docker abstracts host differences)
- Python: 3.11 (via pyenv or official installer); `venv` for project-level isolation
- Package Manager: pip with `requirements.txt` pinned versions; separate requirements files per service
- Docker: Docker Desktop 4.x (includes Docker Compose v2); used for all cloud-side services
- Version Control: Git 2.40+; GitHub as remote; branch-per-feature workflow with PRs
- IDE: VS Code with Python extension, Pylance, and Docker extension; Jupyter extension for notebook work

**Edge Node Environment (Het Bhalala primary):**
- MicroPython: v1.23+ flashed to ESP32 via `esptool.py` (command-line, cross-platform)
- IDE: Thonny 4.x for interactive MicroPython REPL and file upload to ESP32
- Protocol Debugging: MQTT Explorer (GUI) for visualizing MQTT topic traffic during development
- Hardware Simulation: Wokwi (browser-based) for validating MicroPython + SPI + MQTT code before physical hardware arrives

---

### 8.2 Development Tools per Team Member

| Member | Role | Primary Tools |
|---|---|---|
| Parth Gala | RL Agent & Orchestration | PyTorch 2.x, Stable-Baselines3 (SB3), gymnasium, SHAP, Jupyter Notebook, pytest |
| Het Bhalala | Edge IoT & Hardware | Thonny 4.x, esptool.py, MicroPython umqtt.simple, MQTT Explorer, Wokwi simulator, oscilloscope (for SPI signal validation) |
| Rodrigo Chen | Dashboard & Backend | React 18 + Vite + TypeScript, FastAPI 0.110, PostgreSQL 16, Chart.js, Postman, pgAdmin |
| Aruna Samhitha Mutyala | Forecasting & XAI | Prophet (Meta), PyTorch LSTM, pandas, SHAP, scikit-learn, Jupyter Notebook, matplotlib |

All team members additionally use: Git, VS Code, Docker Desktop, MQTT Explorer (for integration testing).

---

### 8.3 Hardware Prototyping Environment

**Physical Hardware (to be ordered by March 14, 2026):**

| Component | Quantity | Purpose | Approximate Cost |
|---|---|---|---|
| ESP32 DevKit V1 | 2 | Edge node microcontrollers | $4 each |
| MAX7219 8-digit 7-segment display module | 4 | Physical price display chain (2 per ESP32) | $1.50 each |
| Full-size breadboard | 2 | Component prototyping | $5 each |
| Jumper wire kit (M-M, M-F, F-F) | 1 | Circuit connections | $8 |
| 5V 2A USB power supply | 2 | ESP32 + MAX7219 chain power | $6 each |
| Micro-USB cable | 2 | ESP32 programming and power | $3 each |
| 0.1µF decoupling capacitors (pack of 50) | 1 | SPI signal integrity on long chains | $4 |

**Total estimated hardware budget: ~$60 for both nodes**

**Software Simulation (pre-hardware fallback):**
- **Wokwi Simulator** (https://wokwi.com): Browser-based ESP32 simulation with MAX7219 support; enables MicroPython + SPI chain code validation before physical hardware arrives; used by Het Bhalala from Sprint 0.

---

### 8.4 Example Programs

Each team member will implement a "hello world" example program for their primary technology stack to verify that the development environment is correctly configured before beginning Sprint 0 development work.

**Parth Gala — CartPole RL with Stable-Baselines3:**
```python
# Verifies: PyTorch, gymnasium, SB3 installation and GPU/CPU training
import gymnasium as gym
from stable_baselines3 import DQN

env = gym.make("CartPole-v1")
model = DQN("MlpPolicy", env, verbose=1)
model.learn(total_timesteps=10_000)
mean_reward, _ = evaluate_policy(model, env, n_eval_episodes=10)
print(f"Mean reward: {mean_reward}")
```

**Parth Gala / Aruna Samhitha Mutyala — SHAP TreeExplainer Hello World:**
```python
# Verifies: SHAP installation and basic explainer API
import shap
import sklearn
from sklearn.ensemble import RandomForestRegressor
import numpy as np

X, y = shap.datasets.california(n_points=500)
model = RandomForestRegressor(n_estimators=50, random_state=42).fit(X, y)
explainer = shap.TreeExplainer(model)
shap_values = explainer.shap_values(X[:5])
shap.summary_plot(shap_values, X[:5])  # renders feature importance chart
```

**Het Bhalala — MicroPython MAX7219 Count 0–9:**
```python
# MicroPython on ESP32 — verifies SPI + MAX7219 chain communication
from machine import Pin, SPI
import max7219

spi = SPI(1, baudrate=10_000_000, polarity=0, phase=0,
          sck=Pin(18), mosi=Pin(23))
cs = Pin(5, Pin.OUT)
display = max7219.Matrix8x8(spi, cs, 4)  # 4 chained modules

for i in range(10):
    display.fill(0)
    display.text(str(i), 0, 0, 1)
    display.show()
    time.sleep(0.5)
```

**Aruna Samhitha Mutyala — Prophet Quickstart:**
```python
# Verifies: Prophet installation and basic time-series fitting
import pandas as pd
from prophet import Prophet

df = pd.read_csv('sample_sales.csv')  # columns: ds (date), y (units sold)
df.columns = ['ds', 'y']
m = Prophet()
m.fit(df)
future = m.make_future_dataframe(periods=30)
forecast = m.predict(future)
m.plot(forecast)  # renders forecast with uncertainty intervals
```

**Rodrigo Chen — FastAPI + WebSocket Tutorial:**
```python
# Verifies: FastAPI installation and WebSocket connection
from fastapi import FastAPI, WebSocket
import uvicorn

app = FastAPI()

@app.websocket("/ws/prices")
async def price_stream(websocket: WebSocket):
    await websocket.accept()
    while True:
        data = await websocket.receive_text()
        await websocket.send_text(f"Echo: {data}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

---

### 8.5 Initial Prototype Milestone (Sprint 1, Week 6 — March 28, 2026)

The Sprint 1 prototype milestone is an end-to-end smoke test of the full vertical slice of the system using a single product and a stub (rule-based) pricing agent:

**Smoke Test Scenario:**
1. A Python stub pricing agent publishes a price update (`{"product_id": "SKU-001", "price": "12.99", "reason": "Inventory high"}`) to the MQTT broker on topic `retail/price/SKU-001`
2. The MQTT broker receives and routes the message
3. An ESP32 edge node (physical or Wokwi) subscribed to `retail/price/SKU-001` receives the message, parses the JSON payload, writes "12.99" to NVS, and displays "12.99" on the MAX7219 7-segment chain
4. The FastAPI backend bridge receives the same MQTT message and pushes it to the React dashboard via WebSocket
5. The React dashboard renders "SKU-001: $12.99" in the live price table with a placeholder SHAP chart

**Success Criteria:**
- Price displayed on MAX7219 within 5 seconds of Python publish
- Price visible on React dashboard within 5 seconds of Python publish
- NVS fallback demonstrated by disconnecting broker and power-cycling ESP32; "12.99" must still display on reconnect

---

## Chapter 9 — Project Schedule

*Note: Class assignments, lectures, and administrative deadlines are excluded from this schedule. The schedule covers only project development work. All dates are estimates and may shift based on hardware delivery timelines.*

### 9.1 CMPE 295A Sprint Schedule (March 1 – May 9, 2026)

**Sprint 0: March 1 – 14, 2026**
*Milestone: All development environments operational; hardware ordered; first MQTT broker-to-client message validated*

| Member | Tasks |
|---|---|
| Parth Gala | Set up Python 3.11 + venv + PyTorch + SB3; implement CartPole DQN hello world; stub out gymnasium retail environment class skeleton |
| Het Bhalala | Flash MicroPython to ESP32 DevKit; wire one MAX7219 module via SPI on breadboard; validate count 0–9 display on hardware (or Wokwi); place hardware order |
| Rodrigo Chen | Scaffold React + Vite + TypeScript frontend; scaffold FastAPI backend; run FastAPI + WebSocket hello world; set up PostgreSQL in Docker |
| Aruna Samhitha Mutyala | Set up Prophet + pandas environment; run Prophet quickstart on public retail dataset; identify and download historical retail dataset for EDA |

---

**Sprint 1: March 15 – 28, 2026**
*Milestone: End-to-end smoke test complete (stub agent → MQTT → ESP32 → MAX7219 → dashboard); Workbook Assignment 1 submitted*

| Member | Tasks |
|---|---|
| Parth Gala | Implement stub rule-based pricing agent (Python); integrate agent with MQTT broker publish; run SHAP TreeExplainer hello world; submit Workbook 1 to Canvas |
| Het Bhalala | Implement MQTT subscriber on ESP32 (umqtt.simple); receive price payload and display on MAX7219; implement NVS write-on-receive; validate smoke test scenario |
| Rodrigo Chen | Implement FastAPI MQTT bridge (subscribes broker, pushes to WebSocket); implement live price table in React dashboard; add placeholder SHAP chart panel |
| Aruna Samhitha Mutyala | Conduct EDA on retail dataset (price distribution, seasonality, demand patterns); run Prophet quickstart on actual dataset; document findings in notebook |

---

**Sprint 2: March 29 – April 11, 2026**
*Milestone: DRL training pipeline running; SHAP module generating real explanation vectors; Prophet forecasting dead-stock risk for test SKUs*

| Member | Tasks |
|---|---|
| Parth Gala | Implement gymnasium retail environment with multi-penalty reward function; begin DQN training (CartPole → custom env); document reward function parameter choices |
| Het Bhalala | Wire 4-display MAX7219 SPI chain (daisy-chain); implement product-addressing logic for multi-display ESP32; test NVS fallback under simulated broker outage |
| Rodrigo Chen | Integrate real SHAP vector data into dashboard SHAP chart (replace placeholder); implement price change history timeline; implement audit log query endpoint |
| Aruna Samhitha Mutyala | Implement SHAP ensemble model for churn risk scoring; generate churn risk signals from retail dataset; integrate churn risk output with reward function input |

---

**Sprint 3: April 12 – 25, 2026**
*Milestone: Full pipeline with trained (or partially trained) RL agent; comparative metrics (RL vs. rule-based) computed; hardware prototype physically integrated*

| Member | Tasks |
|---|---|
| Parth Gala | Compare DQN vs. rule-based baseline: profit, volatility, churn metrics over 1,000 episodes; produce comparison charts for workbook paper section |
| Het Bhalala | Integrate physical ESP32 + 4-display chain prototype with running Docker Compose backend; validate full end-to-end pipeline on physical hardware |
| Rodrigo Chen | Implement manager constraint controls in dashboard (min/max price per SKU); implement dead-stock alert panel; add RBAC stub (admin vs. manager roles) |
| Aruna Samhitha Mutyala | Evaluate Prophet vs. LSTM on retail dataset (RMSE, MAPE); select best forecaster; integrate dead-stock risk score into pricing agent reward function |

---

**Sprint 4: April 26 – May 9, 2026**
*Milestone: 295A prototype demo-ready; 295B plan documented; paper outline complete*

| Member | Tasks |
|---|---|
| Parth Gala | Finalize RL agent for demo; write paper outline (problem, related work, method, experiments sections); update workbook with final sprint results |
| Het Bhalala | Polish hardware prototype (enclosure, cable management, labels); document hardware assembly steps and SPI configuration in workbook Ch.8 |
| Rodrigo Chen | Demo preparation: end-to-end recording, slide deck for advisor; finalize dashboard UI polish; document REST API endpoints |
| Aruna Samhitha Mutyala | Finalize forecaster integration; run final evaluation metrics; contribute forecasting and XAI sections to paper outline |

---

### 9.2 CMPE 295B High-Level Plan (Fall 2026)

*Detailed sprint tasks to be planned at the start of 295B based on 295A outcomes.*

| Sprint | Approximate Dates | High-Level Milestone |
|---|---|---|
| Sprint 5 | Sep 1 – 14, 2026 | Multi-node ESP32 deployment; DDPG agent replacing DQN; full RL training run |
| Sprint 6 | Sep 15 – 28, 2026 | Federated learning prototype (simulated edge nodes); multi-store gymnasium environment |
| Sprint 7 | Sep 29 – Oct 12, 2026 | REST API + RBAC complete; system integration testing across full stack |
| Sprint 8 | Oct 13 – 26, 2026 | Full system performance evaluation; final comparative study (RL vs. baseline; XAI usability) |
| Sprint 9 | Oct 27 – Nov 9, 2026 | Paper writing: introduction, related work, methodology sections |
| Sprint 10 | Nov 10 – 23, 2026 | Paper writing: experiments, results, discussion sections; internal review and revision |
| Sprint 11 | Nov 24 – Dec 7, 2026 | Paper submission to IEEE ICCE 2026 or ACM BuildSys (per advisor guidance); Expo preparation |
| Sprint 12 | Dec 8 – 21, 2026 | SJSU Engineering Expo presentation; final workbook update; project handoff documentation |
