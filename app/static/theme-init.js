// Тема, плотность и акцент из настроек — до первой отрисовки, чтобы экран не мигал
// тёмной темой у того, кто выбрал светлую. Отдельным файлом, а не строкой в
// index.html: CSP (`script-src 'self'`) встроенные скрипты не выполняет, и прежний
// встроенный вариант молча не работал.
(function () {
  try {
    var r = document.documentElement;
    var t = localStorage.getItem("bt-theme");
    if (t !== "dark" && t !== "light") {
      t = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches
        ? "light" : "dark";
    }
    r.setAttribute("data-theme", t);
    r.setAttribute("data-density", localStorage.getItem("bt-density") || "balanced");
    r.style.setProperty("--accent-h", localStorage.getItem("bt-accent") || "154");
  } catch (e) {}
})();
