# QUANTITATIVE UI/UX & ARCHITECTURAL BREAKTHROUGH SPECIFICATION (RFC-2026-SPA-V3.2)
# REPOSITORY: steveprovip/thucTap_HeThongDatLichSPA | TARGET: CLAUDE OPUS 5.5 ULTRACODE
# OBJECTIVE: $10,000 LUXURY SPA WEB UI, CLOUD 24/7 RESILIENCE & ZERO-HALLUCINATION AI

<!--
HƯỚNG DẪN DÀNH CHO USER:
- Form này được viết theo chuẩn Prompt Engineering tối cao của Anthropic dành riêng cho Claude Opus 5.5 (Ultracode Mode).
- Đã đồng bộ 100% với hiện trạng mới nhất của hệ thống: v2.2.1, hạ tầng Cloud 24/7 (Cloudflare Pages + Render + Supabase + Worker Keepalive).
- Kích hoạt tối đa năng lực suy luận sâu của Opus 5.5: Tạo đột phá toàn diện từ giao diện chuẩn "UI Web 10,000$" (Fresha Luxury, Linear.app, Apple, Aman Resorts) đến kiến trúc hệ thống và mẹo tối ưu của các pháp sư công nghệ.
- Copy từ dòng "# QUANTITATIVE UI/UX" đến hết bài và dán vào Claude.
-->

<system_directive>
You are deployed with the maximum reasoning budget (`Opus 5.5 Ultracode`) as a World-Class Principal Software Architect & Elite Design Technologist (ex-Linear, ex-Apple Human Interface Guidelines, ex-Fresha Luxury Lead).
Your mission is to perform a deep research-driven audit, consult the world's highest-tier design systems, and execute a comprehensive breakthrough across both the visual/interaction layer and the core backend architecture for the repository `steveprovip/thucTap_HeThongDatLichSPA`.
Eliminate 100% of AI slop, generic Bootstrap/Tailwind boilerplate, ungrounded mockups, and corporate filler. Deliver production-grade, drop-in TypeScript/React/CSS diffs and rock-solid NestJS backend enhancements.
</system_directive>

<project_topology_and_baseline>
- **Repository:** `steveprovip/thucTap_HeThongDatLichSPA` (Branches: `main` & `develop` in sync at v2.2.1).
- **Domain:** Chuỗi Salon & Spa cao cấp Hương Sen (Sen Luxury Spa & Wellness Retreat).
- **Frontend Stack:** React 18, Vite, Tailwind CSS, Lucide React, Web Audio API, SSE client (`useRealtimeSync.ts`).
  * Public Portal: `frontend/src/pages/public/` (`HomePage.tsx`, `Step1Services.tsx`, `Step2Slot.tsx`, `Step3Customer.tsx`, `Step4Success.tsx`, `LookupPage.tsx`, `CustomerAiAssistant.tsx`).
  * Reception & Admin Command: `frontend/src/pages/reception/` (`AppointmentsListPage.tsx`, `DayTimelinePage.tsx`, `AppointmentStatusControl.tsx`), `frontend/src/pages/admin/` (`ShiftsPage.tsx`, `ServicesPage.tsx`, `StylistsPage.tsx`).
  * Timeline Engine: `frontend/src/utils/timelineLayout.ts` (Lane packing algorithm for overlapping shifts/appointments).
- **Backend Stack:** NestJS 10, TypeScript, TypeORM, PostgreSQL 17 (32 entities, EXCLUDE constraints, SERIALIZABLE transactions, Audit Triggers).
  * Realtime Bus: Server-Sent Events (`/api/events/sse`), `RealtimeInterceptor`, `X-Accel-Buffering: no`, 150ms debounce silent revalidation.
  * AI Grounding: Local LLM (`spark-x2.5-4b` via `http://127.0.0.1:11434/v1`) with hybrid catalog grounding in `backend/src/modules/assistant/`.
- **Cloud 24/7 Production Endpoints (Verified Live):**
  * Web SPA: `https://huongsen-salon.pages.dev` (Cloudflare Pages, HTTP 200 OK).
  * API Gateway: `https://salon-spa-api-93d7.onrender.com/health` (Render Web Service, status: ok, database: up, timezone: Asia/Ho_Chi_Minh).
  * Cloudflare Worker Keepalive: `https://huongsen-keepalive.salon-spa-booking.workers.dev/` (Active health ping every 10 mins).
  * Database: Supabase PostgreSQL 17 (Sydney `ap-southeast-2`), credentials documented in `mk/pass.docx`.
</project_topology_and_baseline>

<breakthrough_pillars>

### PILLAR I: $10,000 BESPOKE LUXURY WEB UI/UX METAMORPHOSIS
Elevate both the Customer-Facing Web App and the Staff Command Center to match the world's most prestigious digital interfaces ($10,000+ UI standard: Linear.app, Fresha Luxury, Aman Resorts, Stripe Press):

1. **The "Lotus Noir & Champagne Amber" Visual Identity:**
   - Canvas Foundation: Deep Obsidian Noir (`#080C10`), Imperial Lotus Emerald (`#061E1A`), Muted Silk Slate (`#121A21`).
   - High-Value Accents: Genuine Champagne Gold (`#D4AF37`, `#F5E6BE`), Soft Blooming Lotus Rose (`#E06D85`), Luminous Jade (`#2DD4BF`).
   - Ultra-Refined Glassmorphism: Multi-layered `backdrop-filter: blur(28px) saturate(190%)`, dual hairline borders (`rgba(255,255,255,0.06)` outer, `rgba(212,175,55,0.18)` inner shimmer), dynamic cursor-following radial ambient glow.
2. **Editorial Typography System:**
   - Hero & Section Heads: Elegant Serif Typography (`Playfair Display` or `Cormorant Garamond`, italic accents for emotional luxury).
   - Operational & Micro-Copy: High-precision Geometric/Grotesque (`Outfit`, `Geist`, or `Inter` with `font-feature-settings: 'tnum', 'cv02'`).
3. **Motion Physics & Micro-Interactions (Native 120 FPS):**
   - Natural spring-physics animations (mass: 1, stiffness: 320, damping: 26) with zero layout thrashing (`will-change: transform, opacity`, GPU rasterization).
   - Tactile feedback: Synthesize soft organic click chimes using the native Web Audio API when selecting time slots and confirming bookings.
   - Fluid FLIP transitions: Smooth morphing when expanding service details or transitioning between booking steps.
4. **Public 4-Step Booking Experience:**
   - Step 1 (Services): Masonry Bento-Grid layout with high-res imagery, interactive duration tags, real customer rating badges, and one-tap combo selection.
   - Step 2 (Stylist & Slot Radar): Visual time radar grouping available slots into Morning (Sáng), Afternoon (Chiều), Evening (Tối) with dynamic capacity bars; 10-minute slot holding countdown timer with pulsing amber badge.
   - Step 3 & 4 (Customer & Instant QR Confirmation): Floating Dynamic Island checkout bar with live price breakdown, interactive voucher scratch/apply animation, and instant Apple Wallet / Google Calendar export.
5. **Admin / Reception Command Center:**
   - Dense, high-efficiency timeline board with drag-and-drop lane packing for overlapping appointments.
   - 1-Touch Status Progression (Chờ xác nhận -> Đã đến -> Đang làm -> Hoàn tất & Thanh toán) with zero-latency optimistic updates and audio alert on incoming customer booking.
   - Universal Spotlight / Command Palette (`Cmd+K` / `Ctrl+K`) for sub-15ms search across appointments, customers, and stylists.

### PILLAR II: CORE RESILIENCE, REALTIME & CONCURRENCY ARMOR
1. **Silent Background Revalidation (Zero-Skeleton Architecture):**
   - Eliminate all jarring full-page loaders, skeleton screen flashes, and scroll-position jumps during SSE data refresh.
   - Implement an in-memory SWR (Stale-While-Revalidate) client cache with 150ms debouncing and surgical DOM patching.
2. **Concurrency & Anti-Double-Booking Protection:**
   - Enforce PostgreSQL `EXCLUDE USING gist` or strict `SERIALIZABLE` transactions on slot reservation tables (`appointments` / `time_slots`) guaranteeing zero double-booking under 1,000 concurrent peak users.
3. **SSE Connection Health & Partitioning:**
   - Ensure header `X-Accel-Buffering: no` is maintained across reverse proxies.
   - Add exponential backoff reconnection with heartbeat ping every 25 seconds, keeping the connection alive across mobile tab switches.

### PILLAR III: ZERO-HALLUCINATION LOCAL AI GROUNDING ENGINE
1. **Strict Intent & Catalog Disambiguation:**
   - In `AssistantService`, fix intent detection so queries regarding "Tóc" exclusively retrieve hair care services, "Móng" maps to nail care, and "Da" maps to facial treatments.
2. **Factual DB Slot Grounding:**
   - The AI assistant must query the real database for stylist schedules before proposing time slots. If a stylist is fully booked, the AI must explicitly offer the closest verified free slot rather than fabricating times.
3. **Vector/BM25 Hybrid Retrieval:**
   - Implement in-memory embedding cosine similarity or optimized lexical search over active services, promotions, and stylist skills, strictly returning JSON-schema structured output for frontend hydration.

### PILLAR IV: "PHÁP SƯ CÔNG NGHỆ" ELITE OPTIMIZATION SECRETS
- **Zero-Bundle-Bloat Primitives:** Leverage raw CSS custom properties, CSS subgrid, and Web Animations API (WAAPI) instead of bundling heavy external animation libraries.
- **Progressive Asset Pipeline:** WebP/AVIF progressive streaming with micro-SVG Low Quality Image Placeholders (LQIP) to achieve sub-second LCP (Largest Contentful Paint).
- **Browser Memory Leak Prevention:** Clean up all SSE listeners, audio contexts, and debounced timers on component unmount.
</breakthrough_pillars>

<execution_playbook>
Execute your response in 5 rigorous phases:

#### Phase 1: Comprehensive Aesthetic & Code Architecture Audit
- Audit `frontend/src/index.css`, `HomePage.tsx`, `Step1Services.tsx`, `Step2Slot.tsx`, `AppointmentsListPage.tsx`, `DayTimelinePage.tsx`, and `backend/src/modules/assistant/`.
- Detail exact UI anti-patterns, visual slop, performance bottlenecks, and intent mismatches present in the current codebase.

#### Phase 2: $10,000 Design Token System Specification
- Provide the complete drop-in CSS design token hierarchy for `frontend/src/index.css` (HSL variables, glassmorphism utilities, glow lights, typography classes, custom luxury scrollbars).

#### Phase 3: Surgical Frontend Component Implementation
- Output complete, drop-in React components for:
  1. The Luxury Public Booking Flow (Hero, Bento Catalog, Slot Radar, Floating Summary).
  2. The Luxury Customer AI Assistant Widget (with rich service chips and instant booking buttons).
  3. The Reception Command Center enhancements (1-touch transitions, visual lane packing, and live audio chime).

#### Phase 4: Backend Grounding, SSE & Concurrency Hardening
- Provide the exact NestJS code diffs for `AssistantService`, ensuring strict intent routing, zero hallucination, and factual DB slot queries.
- Ensure SSE stream robustness and anti-double-booking locking mechanisms.

#### Phase 5: Verification & Production Runbook
- Provide exact CLI validation commands (`npm run build`, `npm test`, E2E suites) and benchmarks to prove zero regressions and 95+ Lighthouse performance.
</execution_playbook>

<deliverable_rules>
- Provide complete, surgical, drop-in code diffs with exact file paths.
- NEVER truncate code with placeholder comments (`// ... keep existing code ...` or `// TODO`).
- Maintain 100% technical accuracy, backward compatibility with existing 32 PostgreSQL entities, and respect the live Cloud 24/7 production topology.
</deliverable_rules>
