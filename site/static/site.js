// Copy buttons, theme toggle, and table-of-contents highlighting. No dependencies.
(function () {
  var root = document.documentElement;

  // Theme: follows the system until the reader picks one.
  var toggle = document.querySelector(".theme");
  if (toggle) {
    toggle.addEventListener("click", function () {
      var dark = root.dataset.theme
        ? root.dataset.theme === "dark"
        : matchMedia("(prefers-color-scheme: dark)").matches;
      root.dataset.theme = dark ? "light" : "dark";
      try { localStorage.setItem("theme", root.dataset.theme); } catch (e) {}
    });
  }

  // Copy buttons. Skips "$ " prompts so a pasted command runs as is.
  document.querySelectorAll("pre").forEach(function (pre) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "copy";
    btn.textContent = "Copy";
    btn.setAttribute("aria-label", "Copy code");
    btn.addEventListener("click", function () {
      var text = pre.querySelector("code").innerText.replace(/^\$ /gm, "").replace(/\n$/, "");
      var done = function () {
        btn.textContent = "Copied";
        btn.classList.add("done");
        setTimeout(function () { btn.textContent = "Copy"; btn.classList.remove("done"); }, 1500);
      };
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(done);
      } else {
        var ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand("copy"); done(); } catch (e) {}
        ta.remove();
      }
    });
    pre.appendChild(btn);
  });

  // Table of contents: closed by default on small screens, current section highlighted.
  var toc = document.querySelector(".toc");
  if (!toc) return;
  if (matchMedia("(max-width: 1039px)").matches) toc.removeAttribute("open");
  toc.addEventListener("click", function (e) {
    if (e.target.tagName === "A" && matchMedia("(max-width: 1039px)").matches) toc.removeAttribute("open");
  });

  var links = Array.prototype.slice.call(toc.querySelectorAll("a"));
  var targets = links.map(function (a) { return document.getElementById(a.hash.slice(1)); });
  var ticking = false;
  function update() {
    ticking = false;
    var current = 0;
    for (var i = 0; i < targets.length; i++) {
      if (targets[i] && targets[i].getBoundingClientRect().top < 120) current = i;
    }
    links.forEach(function (a, i) { a.classList.toggle("active", i === current); });
  }
  addEventListener("scroll", function () {
    if (!ticking) { ticking = true; requestAnimationFrame(update); }
  }, { passive: true });
  update();
})();
