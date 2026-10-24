const button = document.getElementById('open-gemini-sync');
button?.addEventListener('click', () => {
  void chrome.tabs.create({ url: chrome.runtime.getURL('sync/settings.html') });
});
