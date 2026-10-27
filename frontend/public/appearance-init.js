(function () {
  var saved = {};
  try { saved = JSON.parse(localStorage.getItem('flowpilot.appearance') || '{}') || {}; } catch (_) {}
  var choice = ['light', 'dark', 'system'].indexOf(saved.theme) >= 0 ? saved.theme : 'system';
  var dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  var theme = choice === 'system' ? (dark ? 'dark' : 'light') : choice;
  document.documentElement.dataset.theme = theme;
  document.documentElement.dataset.layout = saved.layout === 'focus' ? 'focus' : 'comfortable';
  document.documentElement.style.colorScheme = theme;
})();
