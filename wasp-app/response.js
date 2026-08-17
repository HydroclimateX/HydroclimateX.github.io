async function parseWaspResponse(response) {
  const contentType = response.headers.get('content-type') || '';
  const isJson = /(?:^|\s|;)application\/(?:[\w.+-]+\+)?json(?:\s*;|$)/i.test(contentType);

  if (isJson) {
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.message || `WASP request failed (HTTP ${response.status}).`);
    }
    return payload;
  }

  if (response.status === 413) {
    throw new Error('File too large. The maximum CSV upload is 10 MiB.');
  }
  if (response.status === 429) {
    throw new Error('WASP is busy or the request rate limit was reached. Please wait and try again.');
  }
  throw new Error(`WASP request failed (HTTP ${response.status}).`);
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { parseWaspResponse };
}
