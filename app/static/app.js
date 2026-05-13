/* Vavilon Market — Telegram Mini App frontend */
(function () {
  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) {
    tg.expand();
    tg.ready();
  }

  const initData = tg ? tg.initData : "";
  const CFG = window.__CFG__;
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));
  let user = null;
  let catalog = null;
  let currentLinear = null; // {kind, qty}
  let currentPack = null; // sku
  let currentPreview = null;
  let currentRating = 0;

  function toast(text, isError) {
    const el = $("#toast");
    el.textContent = text;
    el.className = "toast show" + (isError ? " error" : "");
    setTimeout(() => { el.className = "toast"; }, 2600);
  }

  function fmt(n) {
    n = Number(n);
    if (Math.abs(n - Math.round(n)) < 0.005) return Math.round(n) + "₽";
    return n.toFixed(2) + "₽";
  }

  async function api(path, options) {
    options = options || {};
    options.headers = Object.assign(
      { "X-Init-Data": initData },
      options.headers || {}
    );
    if (options.json !== undefined) {
      options.body = JSON.stringify(options.json);
      options.headers["Content-Type"] = "application/json";
      delete options.json;
    }
    const resp = await fetch(path, options);
    const text = await resp.text();
    let data;
    try { data = text ? JSON.parse(text) : null; } catch (_) { data = { raw: text }; }
    if (!resp.ok) {
      const err = new Error(data && data.detail || resp.statusText);
      err.status = resp.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  // ---------------- Tabs ----------------
  $$(".tab").forEach((t) => {
    t.addEventListener("click", () => {
      $$(".tab").forEach((x) => x.classList.remove("active"));
      t.classList.add("active");
      const tab = t.dataset.tab;
      $$(".tab-content").forEach((c) => c.classList.toggle("active", c.dataset.tab === tab));
      if (tab === "orders") loadOrders();
      else if (tab === "ref") loadRef();
    });
  });

  // ---------------- Shop ----------------
  $$(".card").forEach((card) => {
    card.addEventListener("click", () => openCategory(card.dataset.category));
  });

  function openCategory(key) {
    if (key === "robux") openLinear("robux");
    else if (key === "stars") openLinear("stars");
    else openPacks(key);
  }

  function openLinear(kind) {
    currentLinear = {
      kind,
      qty: kind === "robux" ? CFG.robuxMin : CFG.starsMin,
    };
    const isRobux = kind === "robux";
    $("#linear-title").textContent = isRobux ? "🟢 Robux" : "⭐ Telegram Stars";
    $("#linear-rate").textContent = isRobux ? "100 робуксов = 80₽" : "100 звёзд = 150₽";
    const big = isRobux ? CFG.robuxStep * 5 : CFG.starsStep * 5;
    $$('#linear-modal [data-bigstep]').forEach((el) => el.textContent = big);
    $("#linear-modal").classList.remove("hidden");
    renderLinear();
  }
  function renderLinear() {
    const { kind, qty } = currentLinear;
    const isRobux = kind === "robux";
    const rate = isRobux ? 0.8 : 1.5;
    $("#linear-qty").textContent = qty;
    $("#linear-price").textContent = fmt(qty * rate);
  }
  $("#linear-close").addEventListener("click", () => $("#linear-modal").classList.add("hidden"));
  $$('#linear-modal .spinner button').forEach((b) => {
    b.addEventListener("click", () => {
      if (!currentLinear) return;
      const isRobux = currentLinear.kind === "robux";
      const step = (isRobux ? CFG.robuxStep : CFG.starsStep) * Number(b.dataset.step);
      const lo = isRobux ? CFG.robuxMin : CFG.starsMin;
      const hi = isRobux ? CFG.robuxMax : CFG.starsMax;
      currentLinear.qty = Math.max(lo, Math.min(hi, currentLinear.qty + step));
      renderLinear();
    });
  });
  $("#linear-input").addEventListener("change", (e) => {
    if (!currentLinear) return;
    const isRobux = currentLinear.kind === "robux";
    const step = isRobux ? CFG.robuxStep : CFG.starsStep;
    const lo = isRobux ? CFG.robuxMin : CFG.starsMin;
    const hi = isRobux ? CFG.robuxMax : CFG.starsMax;
    let v = parseInt(e.target.value || 0, 10);
    if (!v) return;
    v = Math.max(lo, Math.min(hi, Math.floor(v / step) * step || step));
    currentLinear.qty = v;
    e.target.value = "";
    renderLinear();
  });
  $("#linear-buy").addEventListener("click", async () => {
    if (!currentLinear) return;
    try {
      const preview = await api("/api/preview", {
        method: "POST",
        json: { sku: currentLinear.kind, quantity: currentLinear.qty },
      });
      currentPreview = preview;
      $("#linear-modal").classList.add("hidden");
      openTarget(preview);
    } catch (e) { toast(e.message, true); }
  });

  function openPacks(key) {
    if (!catalog) return;
    const packs = catalog[key] || [];
    $("#packs-title").textContent = key === "brawl" ? "🎮 Brawl Stars" : "👑 Clash Royale";
    const list = $("#packs-list");
    list.innerHTML = "";
    packs.forEach((p) => {
      const el = document.createElement("button");
      el.className = "pack-item";
      el.innerHTML = `<span>${p.title}</span><b>${p.price_pretty}</b>`;
      el.addEventListener("click", async () => {
        try {
          const preview = await api("/api/preview", {
            method: "POST",
            json: { sku: p.sku, quantity: 1 },
          });
          currentPreview = preview;
          $("#packs-modal").classList.add("hidden");
          openTarget(preview);
        } catch (e) { toast(e.message, true); }
      });
      list.appendChild(el);
    });
    $("#packs-modal").classList.remove("hidden");
  }
  $("#packs-close").addEventListener("click", () => $("#packs-modal").classList.add("hidden"));

  function openTarget(preview) {
    $("#target-title").textContent = preview.title;
    $("#target-price").textContent = preview.price_pretty;
    const sku = preview.sku;
    let hint = "Введи данные для доставки.";
    let placeholder = "username / тег";
    if (sku === "robux") { hint = "Введи свой никнейм Roblox (без @)."; placeholder = "RobloxNickname"; }
    else if (sku === "stars") { hint = "Введи @username получателя звёзд."; placeholder = "@username"; }
    else if (sku.startsWith("brawl_") || sku.startsWith("clash_")) {
      hint = "Введи свой игровой тег (например #2YGQRJ9V) или почту Supercell ID.";
      placeholder = "#тег или email";
    }
    $("#target-hint").textContent = hint;
    const input = $("#target-input");
    input.placeholder = placeholder;
    input.value = "";
    $("#target-modal").classList.remove("hidden");
    setTimeout(() => input.focus(), 100);
  }
  $("#target-close").addEventListener("click", () => $("#target-modal").classList.add("hidden"));
  $("#target-confirm").addEventListener("click", async () => {
    if (!currentPreview) return;
    const target = $("#target-input").value.trim();
    if (target.length < 2) { toast("Введи данные доставки", true); return; }
    const btn = $("#target-confirm");
    btn.disabled = true;
    try {
      const res = await api("/api/buy", {
        method: "POST",
        json: { sku: currentPreview.sku, quantity: currentPreview.quantity, target },
      });
      if (res.ok) {
        toast(`Заказ #${res.order_id} принят ✅`);
        user.balance = res.balance;
        user.balance_pretty = res.balance_pretty;
        $("#balance").textContent = res.balance_pretty;
        $("#target-modal").classList.add("hidden");
      }
    } catch (e) {
      if (e.status === 402) {
        toast(`Не хватает ${fmt(e.data.short)} — пополни баланс`, true);
      } else {
        toast(e.message || "Ошибка", true);
      }
    } finally {
      btn.disabled = false;
    }
  });

  // ---------------- Balance ----------------
  $$('.preset').forEach((b) => {
    b.addEventListener("click", () => {
      $$('.preset').forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      $("#deposit-amount").value = b.dataset.amount;
    });
  });
  $("#deposit-go").addEventListener("click", async () => {
    const amount = parseFloat($("#deposit-amount").value);
    if (!amount || amount < CFG.depositMin || amount > CFG.depositMax) {
      toast(`Сумма должна быть от ${CFG.depositMin}₽ до ${CFG.depositMax}₽`, true);
      return;
    }
    const btn = $("#deposit-go");
    btn.disabled = true;
    try {
      const res = await api("/api/deposit", {
        method: "POST",
        json: { amount: String(amount) },
      });
      const html = `
        <a href="${res.pay_url}" target="_blank" rel="noopener">💳 Оплатить ${res.amount_pretty}</a>
        <p class="muted" style="margin-top:8px">После оплаты баланс зачислится автоматически в течение минуты.</p>
      `;
      $("#deposit-result").innerHTML = html;
      if (tg && tg.openLink) {
        tg.openLink(res.pay_url, { try_instant_view: false });
      }
    } catch (e) {
      if (e.status === 503) toast("Криптовалютные платежи временно недоступны", true);
      else toast(e.message || "Ошибка", true);
    } finally {
      btn.disabled = false;
    }
  });

  // ---------------- Orders ----------------
  async function loadOrders() {
    try {
      const res = await api("/api/orders");
      const list = $("#orders-list");
      list.innerHTML = "";
      if (!res.orders.length) {
        list.innerHTML = '<p class="muted">У тебя пока нет заказов.</p>';
        return;
      }
      res.orders.forEach((o) => {
        const card = document.createElement("div");
        card.className = "order-card";
        card.innerHTML = `
          <div class="order-head">
            <b>#${o.id} — ${o.title}</b>
            <span class="order-status status-${o.status}">${o.status}</span>
          </div>
          <div class="order-target">→ ${o.target}</div>
          <div class="muted">${new Date(o.created_at).toLocaleString("ru-RU")} • ${o.price_pretty}</div>
        `;
        list.appendChild(card);
      });
    } catch (e) { toast(e.message, true); }
  }

  // ---------------- Referral ----------------
  async function loadRef() {
    try {
      const r = await api("/api/referral");
      $("#ref-code").textContent = r.code;
      $("#ref-link").value = r.link;
      $("#ref-count").textContent = r.referrals;
      $("#ref-earned").textContent = r.earned_pretty;
    } catch (e) { toast(e.message, true); }
  }
  $("#ref-share").addEventListener("click", () => {
    const link = $("#ref-link").value;
    if (tg && tg.openTelegramLink) {
      const text = encodeURIComponent(
        `Залетай в Vavilon Market — Robux, Telegram Stars, Brawl и Clash дешевле. По моей ссылке ` + link
      );
      tg.openTelegramLink(`https://t.me/share/url?url=${encodeURIComponent(link)}&text=${text}`);
      return;
    }
    if (navigator.share) {
      navigator.share({ url: link, title: "Vavilon Market" }).catch(() => {});
      return;
    }
    navigator.clipboard.writeText(link).then(() => toast("Ссылка скопирована"));
  });

  // ---------------- Review ----------------
  const stars = $$('#rating span');
  stars.forEach((s) => {
    s.addEventListener("click", () => {
      currentRating = Number(s.dataset.r);
      stars.forEach((x) => x.classList.toggle("on", Number(x.dataset.r) <= currentRating));
      stars.forEach((x) => x.textContent = Number(x.dataset.r) <= currentRating ? "★" : "☆");
    });
  });
  $("#review-photo").addEventListener("change", (e) => {
    const file = e.target.files[0];
    $("#review-file-name").textContent = file ? file.name : "";
  });
  $("#review-submit").addEventListener("click", async () => {
    const text = $("#review-text").value.trim();
    if (!currentRating) { toast("Поставь оценку", true); return; }
    if (text.length < 5) { toast("Напиши пару слов", true); return; }
    const btn = $("#review-submit");
    btn.disabled = true;
    try {
      const fd = new FormData();
      fd.append("rating", currentRating);
      fd.append("text", text);
      const photo = $("#review-photo").files[0];
      if (photo) fd.append("photo", photo);
      const resp = await fetch("/api/review", {
        method: "POST",
        headers: { "X-Init-Data": initData },
        body: fd,
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || resp.statusText);
      }
      toast("Спасибо за отзыв! 💛");
      $("#review-text").value = "";
      $("#review-photo").value = "";
      $("#review-file-name").textContent = "";
      currentRating = 0;
      stars.forEach((x) => { x.classList.remove("on"); x.textContent = "☆"; });
    } catch (e) { toast(e.message, true); }
    finally { btn.disabled = false; }
  });

  // ---------------- Boot ----------------
  async function boot() {
    try {
      const [me, cat] = await Promise.all([api("/api/me"), api("/api/catalog")]);
      user = me.user;
      catalog = cat;
      $("#balance").textContent = user.balance_pretty;
    } catch (e) {
      $("#balance").textContent = "—";
      if (!initData) {
        toast("Открой страницу из бота Telegram", true);
      } else {
        toast(e.message, true);
      }
    }
  }
  boot();
})();
