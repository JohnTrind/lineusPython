// WebSocket client with automatic reconnection.

export class WSClient {
  constructor({ onMessage, onOpen, onClose }) {
    this.onMessage = onMessage;
    this.onOpen = onOpen;
    this.onClose = onClose;
    this.ws = null;
    this.retryMs = 500;
    this._connect();
  }

  _connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    this.ws = new WebSocket(`${proto}://${location.host}/ws`);
    this.ws.addEventListener('open', () => {
      this.retryMs = 500;
      this.onOpen?.();
    });
    this.ws.addEventListener('message', (ev) => {
      try {
        this.onMessage(JSON.parse(ev.data));
      } catch { /* ignore malformed frames */ }
    });
    this.ws.addEventListener('close', () => {
      this.onClose?.();
      setTimeout(() => this._connect(), this.retryMs);
      this.retryMs = Math.min(this.retryMs * 2, 5000);
    });
    this.ws.addEventListener('error', () => this.ws.close());
  }

  send(obj) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(obj));
      return true;
    }
    return false;
  }
}
