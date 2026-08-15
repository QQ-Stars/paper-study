function configuredPort(value) {
  const number = Number(value);
  return Number.isInteger(number) && number >= 0 && number <= 65535 ? number : value;
}

function listenServer(server, port, { host, send, onListening } = {}) {
  if (!server || typeof server.listen !== 'function') {
    throw new TypeError('server must be an http.Server');
  }
  const listening = () => {
    const address = server.address();
    if (typeof send === 'function') send({ type: 'legacy-server-ready', address });
    if (typeof onListening === 'function') onListening(address);
  };
  const normalizedPort = configuredPort(port);
  return host
    ? server.listen(normalizedPort, host, listening)
    : server.listen(normalizedPort, listening);
}

module.exports = { listenServer };
