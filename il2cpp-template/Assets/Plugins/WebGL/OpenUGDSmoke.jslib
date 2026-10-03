// Lets the smoke player publish its one-line summary as the page title, so a headless browser can read
// document.title instead of scraping the console. Called from Boot.cs through [DllImport("__Internal")].
mergeInto(LibraryManager.library, {
  OpenUGDSmokeSetTitle: function (text) {
    document.title = UTF8ToString(text);
  }
});
