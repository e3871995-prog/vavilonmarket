/* Vavilon Market — Telegram Mini App frontend */
(function () {
  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) { try { tg.expand(); tg.ready(); tg.setHeaderColor && tg.setHeaderColor('#0d1117'); } catch (_) {} }
  const initData = tg ? tg.initData : "";
  const IS_TG = !!(tg && initData);
  const BOT_LINK = "https://t.me/Vavilon_Shop_Bot";
  const SESSION_KEY = "vm_web_session";
  let webSession = !IS_TG ? (localStorage.getItem(SESSION_KEY) || "") : "";
  function isAuthed() { return IS_TG || !!webSession; }
  if (!IS_TG) {
    document.body.classList.add("vm-in-browser");
    const banner = document.getElementById("vm-browser-banner");
    if (banner) banner.style.display = "";
  }
  function updateLoginButton() {
    const lin = document.getElementById("vm-login-btn");
    const lout = document.getElementById("vm-logout-btn");
    if (!lin || !lout) return;
    if (IS_TG) { lin.style.display = "none"; lout.style.display = "none"; return; }
    lin.style.display = webSession ? "none" : "";
    lout.style.display = webSession ? "" : "none";
  }
  function requireAuth(intent) {
    if (isAuthed()) return true;
    toast("Сначала войди — введи @username", "error");
    openSheet("vm-login-sheet");
    return false;
  }
  const CFG = window.__CFG__ || {};

  const $ = (s, root) => (root || document).querySelector(s);
  const $$ = (s, root) => Array.from((root || document).querySelectorAll(s));

  let me = null;
  let catalog = null;
  let referral = null;
  let activeSheet = null;
  let robux = { qty: CFG.robuxMin };
  let stars = { qty: CFG.starsMin };
  let depositAmount = null;
  let buyDraft = null; // { sku, quantity, price, title, kind }
  let reviewRating = 0;
  let reviewPhoto = null;

  function fmt(n) {
    n = Number(n);
    if (Math.abs(n - Math.round(n)) < 0.005) return Math.round(n).toLocaleString('ru-RU') + "₽";
    return n.toFixed(2).replace('.', ',') + "₽";
  }

  function toast(text, kind) {
    const el = $("#vm-toast");
    el.textContent = text;
    el.className = "vm-toast show" + (kind === 'error' ? " error" : (kind === 'success' ? " success" : ""));
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { el.className = "vm-toast"; }, 2800);
  }

  async function api(path, opts) {
    opts = opts || {};
    const authHeaders = IS_TG ? { "X-Init-Data": initData } : (webSession ? { "X-Web-Session": webSession } : {});
    opts.headers = Object.assign({}, authHeaders, opts.headers || {});
    if (opts.json !== undefined) {
      opts.body = JSON.stringify(opts.json);
      opts.headers["Content-Type"] = "application/json";
      delete opts.json;
    }
    const r = await fetch(path, opts);
    const text = await r.text();
    let data;
    try { data = text ? JSON.parse(text) : null; } catch (_) { data = { raw: text }; }
    if (!r.ok) {
      const e = new Error((data && (data.detail || data.error)) || r.statusText);
      e.status = r.status; e.data = data;
      throw e;
    }
    return data;
  }

  // ============= NAVIGATION =============
  function showPage(page) {
    $$(".vm-page").forEach(p => p.classList.toggle("active", p.dataset.page === page));
    $$(".vm-bn-item").forEach(b => b.classList.toggle("active", b.dataset.page === page));
    window.scrollTo({ top: 0, behavior: "instant" });
    if (page === "profile") refreshProfile();
  }
  $$(".vm-bn-item").forEach(b => b.addEventListener("click", () => showPage(b.dataset.page)));

  // category from home → catalog tab
  function showCat(cat) {
    showPage("catalog");
    $$(".vm-tab").forEach(t => t.classList.toggle("active", t.dataset.cat === cat));
    $$(".vm-tab-content").forEach(c => c.classList.toggle("active", c.dataset.cat === cat));
  }
  $$(".vm-tab").forEach(t => t.addEventListener("click", () => showCat(t.dataset.cat)));

  // delegate "data-action"
  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-action]");
    if (!t) return;
    const act = t.dataset.action;
    if (act === "go-cat") showCat(t.dataset.cat);
    else if (act === "go-page") showPage(t.dataset.page);
    else if (act === "open-deposit") { if (!requireAuth("deposit")) return; openSheet("vm-deposit-sheet"); }
    else if (act === "open-review") { if (!requireAuth("review")) return; openSheet("vm-review-sheet"); }
    else if (act === "open-support") openSheet("vm-support-sheet");
    else if (act === "open-login") openSheet("vm-login-sheet");
    else if (act === "do-logout") doLogout();
  });

  // ============= SHEETS =============
  function openSheet(id) {
    closeSheet();
    $("#vm-overlay").classList.add("active");
    const sheet = document.getElementById(id);
    sheet.classList.add("active");
    activeSheet = sheet;
  }
  function closeSheet() {
    if (activeSheet) activeSheet.classList.remove("active");
    $("#vm-overlay").classList.remove("active");
    activeSheet = null;
  }
  $("#vm-overlay").addEventListener("click", closeSheet);
  $("#vm-target-close").addEventListener("click", closeSheet);
  $("#vm-deposit-close").addEventListener("click", closeSheet);
  $("#vm-review-close").addEventListener("click", closeSheet);
  $("#vm-support-close").addEventListener("click", closeSheet);
  const _loginClose = document.getElementById("vm-login-close");
  if (_loginClose) _loginClose.addEventListener("click", () => { stopLoginPolling(); closeSheet(); });

  // ============= SLIDER DOTS =============
  function initSlider() {
    const slider = $("#vm-slider");
    const dots = $("#vm-slider-dots");
    const slides = $$(".vm-slide", slider);
    slides.forEach((_, i) => {
      const d = document.createElement("div");
      d.className = "vm-slider-dot" + (i === 0 ? " active" : "");
      d.addEventListener("click", () => {
        slides[i].scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
      });
      dots.appendChild(d);
    });
    slider.addEventListener("scroll", () => {
      const w = slider.clientWidth;
      const idx = Math.round(slider.scrollLeft / Math.max(1, w - 32));
      $$(".vm-slider-dot", dots).forEach((d, i) => d.classList.toggle("active", i === idx));
    });
  }

  // ============= LINEAR (Robux / Stars) =============
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function renderRobux() {
    $("#vm-robux-qty").textContent = robux.qty.toLocaleString('ru-RU');
    $("#vm-robux-price").textContent = fmt(robux.qty * 0.8);
    $$("#vm-robux-presets .vm-preset").forEach(p => p.classList.toggle("active", Number(p.dataset.v) === robux.qty));
  }
  function renderStars() {
    $("#vm-stars-qty").textContent = stars.qty.toLocaleString('ru-RU');
    $("#vm-stars-price").textContent = fmt(stars.qty * 1.5);
    $$("#vm-stars-presets .vm-preset").forEach(p => p.classList.toggle("active", Number(p.dataset.v) === stars.qty));
  }
  function bindLinear(kind) {
    const isRobux = kind === "robux";
    const state = isRobux ? robux : stars;
    const step = isRobux ? CFG.robuxStep : CFG.starsStep;
    const lo = isRobux ? CFG.robuxMin : CFG.starsMin;
    const hi = isRobux ? CFG.robuxMax : CFG.starsMax;
    const presetEl = $(`#vm-${kind}-presets`);
    const presets = isRobux ? [100, 400, 800, 1000, 2200, 4500] : [100, 250, 500, 1000, 2500, 5000];
    presets.forEach(v => {
      const b = document.createElement("div");
      b.className = "vm-preset";
      b.dataset.v = v;
      b.textContent = v.toLocaleString('ru-RU');
      b.addEventListener("click", () => { state.qty = v; isRobux ? renderRobux() : renderStars(); });
      presetEl.appendChild(b);
    });
    const card = $(`#vm-${kind}-buy`).closest(".vm-linear-card");
    $$(".vm-spinner button", card).forEach(b => {
      b.addEventListener("click", () => {
        const sign = b.dataset.spin === "+1" ? 1 : -1;
        state.qty = clamp(state.qty + sign * step, lo, hi);
        isRobux ? renderRobux() : renderStars();
      });
    });
    const inp = $(`#vm-${kind}-input`);
    inp.addEventListener("change", () => {
      let v = parseInt(inp.value || "0", 10);
      if (!v) return;
      v = Math.round(v / step) * step;
      v = clamp(v, lo, hi);
      state.qty = v;
      inp.value = "";
      isRobux ? renderRobux() : renderStars();
    });
    $(`#vm-${kind}-buy`).addEventListener("click", () => startBuy({ sku: kind, quantity: state.qty, kind }));
  }
  bindLinear("robux"); bindLinear("stars"); renderRobux(); renderStars();

  // ============= PACKS (Brawl / Clash) =============
  async function loadCatalog() {
    try {
      catalog = await api("/api/catalog");
      renderPacks();
    } catch (e) { toast("Каталог не загрузился: " + e.message, "error"); }
  }
  function renderPacks() {
    function render(listId, packs, emoji) {
      const root = $(listId);
      root.innerHTML = "";
      packs.forEach(p => {
        const el = document.createElement("div");
        el.className = "vm-product-card";
        el.innerHTML = `
          <div class="vm-product-emoji">${emoji}</div>
          <div class="vm-product-info">
            <div class="vm-product-name">${p.title}</div>
            <div class="vm-product-desc">SKU: ${p.sku}</div>
          </div>
          <div class="vm-product-price">${p.price_pretty}</div>
        `;
        el.addEventListener("click", () => startBuy({ sku: p.sku, quantity: 1, kind: p.sku.startsWith("brawl") ? "brawl" : "clash", title: p.title, price: p.price_pretty }));
        root.appendChild(el);
      });
    }
    render("#vm-brawl-list", catalog.brawl, "💎");
    render("#vm-clash-list", catalog.clash, "💎");
  }

  // ============= BUY FLOW =============
  async function startBuy(draft) {
    if (!requireAuth("buy")) return;
    if (!me) { toast("Подожди секунду, загружаем профиль", "error"); refreshProfile(); return; }
    try {
      const preview = await api("/api/preview", { method: "POST", json: { sku: draft.sku, quantity: draft.quantity } });
      buyDraft = Object.assign({}, draft, preview);
      $("#vm-target-title").textContent = preview.title;
      let sub = "";
      if (draft.kind === "robux") sub = "Укажи Roblox username (логин в Roblox), куда отправить робуксы";
      else if (draft.kind === "stars") sub = "Укажи @username получателя в Telegram (или свой)";
      else sub = "Укажи свой @username в Telegram — мы свяжемся для доставки";
      $("#vm-target-sub").textContent = sub;
      $("#vm-target-preview").textContent = `${preview.title} — ${preview.price_pretty}. Будет списано с баланса.`;
      $("#vm-target-input").value = (draft.kind === "stars" && me.username) ? "@" + me.username : "";
      openSheet("vm-target-sheet");
    } catch (e) { toast(e.message, "error"); }
  }
  $("#vm-target-submit").addEventListener("click", async () => {
    if (!buyDraft) return;
    const target = $("#vm-target-input").value.trim();
    if (target.length < 2) { toast("Укажи получателя", "error"); return; }
    try {
      const r = await api("/api/buy", { method: "POST", json: { sku: buyDraft.sku, quantity: buyDraft.quantity, target } });
      if (r.ok) {
        toast("Заказ #" + r.order_id + " отправлен админу", "success");
        closeSheet();
        if (me) { me.balance = r.balance; me.balance_pretty = r.balance_pretty; refreshProfile(); }
      } else if (r && r.error === "insufficient_balance") {
        toast("Не хватает " + fmt(r.short) + " — пополни баланс", "error");
        closeSheet();
        openSheet("vm-deposit-sheet");
      } else {
        toast("Ошибка заказа", "error");
      }
    } catch (e) {
      if (e.status === 402) {
        toast("Не хватает баланса — пополни", "error");
        closeSheet();
        openSheet("vm-deposit-sheet");
      } else {
        toast(e.message, "error");
      }
    }
  });

  // ============= DEPOSIT FLOW =============
  function initDepositPresets() {
    const root = $("#vm-deposit-presets");
    (CFG.depositPresets || [100, 250, 500, 1000, 2000, 5000]).forEach(v => {
      const b = document.createElement("div");
      b.className = "vm-preset";
      b.dataset.v = v;
      b.textContent = fmt(v);
      b.addEventListener("click", () => {
        depositAmount = v;
        $("#vm-deposit-input").value = v;
        $$("#vm-deposit-presets .vm-preset").forEach(x => x.classList.toggle("active", Number(x.dataset.v) === v));
      });
      root.appendChild(b);
    });
    $("#vm-deposit-input").addEventListener("input", () => {
      depositAmount = parseInt($("#vm-deposit-input").value || "0", 10);
      $$("#vm-deposit-presets .vm-preset").forEach(x => x.classList.toggle("active", Number(x.dataset.v) === depositAmount));
    });
  }
  initDepositPresets();
  $("#vm-deposit-submit").addEventListener("click", async () => {
    if (!requireAuth("deposit")) return;
    const amt = parseInt($("#vm-deposit-input").value || "0", 10);
    if (!amt || amt < CFG.depositMin) { toast(`Минимум ${CFG.depositMin}₽`, "error"); return; }
    if (amt > CFG.depositMax) { toast(`Максимум ${CFG.depositMax}₽`, "error"); return; }
    try {
      const r = await api("/api/deposit", { method: "POST", json: { amount: amt } });
      if (r.pay_url) {
        if (tg) tg.openLink(r.pay_url);
        else window.open(r.pay_url, "_blank");
        toast("Счёт создан, оплати по ссылке", "success");
        closeSheet();
      }
    } catch (e) {
      if (e.status === 503) toast("Платёжная система не настроена админом", "error");
      else toast(e.message, "error");
    }
  });

  // ============= REVIEW FLOW =============
  $$("#vm-review-stars span").forEach(s => {
    s.addEventListener("click", () => {
      reviewRating = Number(s.dataset.rate);
      $$("#vm-review-stars span").forEach(x => x.classList.toggle("active", Number(x.dataset.rate) <= reviewRating));
    });
  });
  $("#vm-review-photo").addEventListener("change", (e) => {
    reviewPhoto = e.target.files[0] || null;
    const lbl = e.target.closest(".vm-file");
    lbl.classList.toggle("has", !!reviewPhoto);
    if (reviewPhoto) lbl.querySelector("span").textContent = "📎 " + reviewPhoto.name;
    else lbl.querySelector("span").textContent = "📎 Прикрепить скриншот";
  });
  $("#vm-review-submit").addEventListener("click", async () => {
    if (!requireAuth("review")) return;
    if (reviewRating < 1) { toast("Поставь оценку", "error"); return; }
    const text = $("#vm-review-text").value.trim();
    if (!text) { toast("Напиши пару слов", "error"); return; }
    const fd = new FormData();
    fd.append("rating", reviewRating);
    fd.append("text", text);
    if (reviewPhoto) fd.append("photo", reviewPhoto);
    try {
      const reviewHeaders = IS_TG ? { "X-Init-Data": initData } : (webSession ? { "X-Web-Session": webSession } : {});
      const r = await fetch("/api/review", { method: "POST", headers: reviewHeaders, body: fd });
      if (!r.ok) {
        const t = await r.text();
        throw new Error(t || "ошибка");
      }
      toast("Спасибо! Отзыв уходит в канал", "success");
      $("#vm-review-text").value = "";
      reviewPhoto = null; reviewRating = 0;
      $$("#vm-review-stars span").forEach(x => x.classList.remove("active"));
      $(".vm-file").classList.remove("has");
      $(".vm-file span").textContent = "📎 Прикрепить скриншот";
      closeSheet();
    } catch (e) { toast("Не удалось отправить", "error"); }
  });

  // ============= PROFILE =============
  async function refreshProfile() {
    if (!isAuthed()) {
      $("#vm-pf-name").textContent = "Гость";
      $("#vm-pf-id").textContent = "Войди через бота, чтобы видеть баланс";
      $("#vm-pf-avatar").textContent = "👤";
      $("#vm-pf-balance").textContent = "—";
      $("#vm-pf-orders").textContent = "0";
      $("#vm-pf-refs").textContent = "0";
      $("#vm-pf-spent").textContent = "—";
      $("#vm-pf-ref-link").value = "";
      $("#vm-pf-ref-link").placeholder = "Нажми «Войти» вверху";
      $("#vm-pf-ref-stats").textContent = "Чтобы видеть реф ссылку, войди.";
      const root = $("#vm-pf-orders-list");
      root.innerHTML = '<div class="vm-empty">Нажми «Войти» чтобы увидеть заказы</div>';
      return;
    }
    try {
      const [m, orders, ref] = await Promise.all([
        api("/api/me"),
        api("/api/orders"),
        api("/api/referral"),
      ]);
      me = m.user;
      referral = ref;
      const first = (me.first_name || me.username || "Пользователь").trim();
      $("#vm-pf-name").textContent = first;
      $("#vm-pf-id").textContent = "TG ID: " + me.id;
      $("#vm-pf-avatar").textContent = (first[0] || "👤").toUpperCase();
      $("#vm-pf-balance").textContent = me.balance_pretty;
      $("#vm-pf-orders").textContent = orders.orders.length;
      $("#vm-pf-refs").textContent = ref.referrals;
      const spent = orders.orders
        .filter(o => o.status !== "cancelled")
        .reduce((s, o) => s + Number(o.price), 0);
      $("#vm-pf-spent").textContent = fmt(spent);
      $("#vm-pf-ref-link").value = ref.link;
      $("#vm-pf-ref-stats").textContent = `+${ref.bonus}₽ за каждого друга, купившего на ${ref.threshold}₽. Приведено: ${ref.referrals} · Заработано: ${ref.earned_pretty}`;
      renderOrders(orders.orders);
    } catch (e) {
      if (e.status === 401 && webSession) {
        // Session was rejected / expired on server. Clear & require login again.
        webSession = ""; localStorage.removeItem(SESSION_KEY); updateLoginButton();
        toast("Сессия истекла, войди заново", "error");
        refreshProfile();
      } else {
        toast("Не удалось загрузить профиль: " + e.message, "error");
      }
    }
  }
  function renderOrders(list) {
    const root = $("#vm-pf-orders-list");
    if (!list.length) {
      root.innerHTML = '<div class="vm-empty">Заказов пока нет</div>';
      return;
    }
    root.innerHTML = "";
    list.slice(0, 20).forEach(o => {
      const el = document.createElement("div");
      el.className = "vm-order";
      const statusLabel = o.status === "paid" ? "ожидание" : o.status === "delivered" ? "выдан" : (o.status === "cancelled" ? "отменён" : o.status);
      const statusCls = "vm-status-" + o.status;
      el.innerHTML = `
        <div class="vm-order-info">
          <div class="vm-order-title">${o.title}</div>
          <div class="vm-order-meta">#${o.id} · ${new Date(o.created_at).toLocaleDateString('ru-RU')} · <span class="${statusCls}">${statusLabel}</span></div>
        </div>
        <div class="vm-order-price">${o.price_pretty}</div>
      `;
      root.appendChild(el);
    });
  }

  $("#vm-pf-ref-copy").addEventListener("click", () => {
    if (!requireAuth("ref")) return;
    if (!referral) return;
    navigator.clipboard.writeText(referral.link).then(
      () => toast("Ссылка скопирована", "success"),
      () => toast("Не удалось скопировать", "error"),
    );
  });
  $("#vm-pf-ref-share").addEventListener("click", () => {
    if (!requireAuth("ref")) return;
    if (!referral) return;
    const url = "https://t.me/share/url?url=" + encodeURIComponent(referral.link) +
                "&text=" + encodeURIComponent(`🛒 VavilonMarket — донат дешевле. Регайся по моей ссылке и получи бонус.`);
    if (tg) tg.openTelegramLink(url);
    else window.open(url, "_blank");
  });

  // ============= LOGIN FLOW (browser only) =============
  let _loginPoll = null;
  function stopLoginPolling() { if (_loginPoll) { clearInterval(_loginPoll); _loginPoll = null; } }
  async function doLogout() {
    try { await api("/api/web/logout", { method: "POST" }); } catch (_) {}
    webSession = ""; localStorage.removeItem(SESSION_KEY);
    updateLoginButton(); me = null; referral = null; refreshProfile();
    toast("Вышел из аккаунта", "success");
  }
  const _loginSubmit = document.getElementById("vm-login-submit");
  if (_loginSubmit) {
    _loginSubmit.addEventListener("click", async () => {
      const raw = (document.getElementById("vm-login-username").value || "").trim();
      if (raw.length < 3) { toast("Введи свой @username", "error"); return; }
      _loginSubmit.disabled = true; _loginSubmit.textContent = "Отправляем...";
      try {
        const r = await fetch("/api/web/login_start", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username: raw }),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) { toast(data.detail || "Ошибка", "error"); return; }
        const pendingToken = data.token;
        document.getElementById("vm-login-step1").style.display = "none";
        document.getElementById("vm-login-step2").style.display = "";
        stopLoginPolling();
        let attempts = 0;
        _loginPoll = setInterval(async () => {
          attempts += 1;
          if (attempts > 120) { stopLoginPolling(); toast("Сессия истекла", "error"); return; }
          try {
            const rs = await fetch("/api/web/login_status?token=" + encodeURIComponent(pendingToken));
            const ds = await rs.json();
            if (ds.status === "confirmed") {
              stopLoginPolling();
              webSession = pendingToken; localStorage.setItem(SESSION_KEY, webSession);
              updateLoginButton(); closeSheet();
              toast("Привет! Сессия активна", "success");
              await refreshProfile();
            } else if (ds.status === "rejected" || ds.status === "expired") {
              stopLoginPolling();
              toast("Вход отклонён", "error");
              document.getElementById("vm-login-step1").style.display = "";
              document.getElementById("vm-login-step2").style.display = "none";
            }
          } catch (_) {}
        }, 1500);
      } catch (e) { toast(e.message || "Ошибка", "error"); }
      finally { _loginSubmit.disabled = false; _loginSubmit.textContent = "Отправить код в Telegram"; }
    });
  }

  // ============= INIT =============
  initSlider();
  loadCatalog();
  updateLoginButton();
  refreshProfile();
})();
