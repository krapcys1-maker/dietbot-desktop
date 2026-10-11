// Ustawia motyw przed pierwszym rysowaniem strony (bez błysku jasnego tła). Ta sama logika co w App.tsx.
(function () {
  try {
    var chosen = localStorage.getItem("porcja:motyw");
    var theme = chosen === "\"dark\"" || chosen === "dark" ? "dark"
      : chosen === "\"light\"" || chosen === "light" ? "light"
      : (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    document.documentElement.dataset.theme = theme;
  } catch (e) {
    /* pamięć przeglądarki niedostępna — React ustawi motyw po starcie */
  }
})();
