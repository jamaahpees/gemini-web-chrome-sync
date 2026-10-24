import { createSyncWorker } from './worker.js';

if (
  typeof chrome !== 'undefined' &&
  chrome.runtime.getManifest().name === 'Cookie-Editor Gemini Sync' &&
  !chrome.extension.inIncognitoContext
) {
  createSyncWorker(chrome);
}
