// sidetap.io Pro waitlist: POST {email} -> one notification email via Resend.
// No storage, no dependencies. RESEND_API_KEY is set on the Vercel project.

const ALLOWED_ORIGINS = new Set([
  'https://sidetap.io',
  'https://www.sidetap.io',
]);

module.exports = async (req, res) => {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST');
    return res.status(405).json({ error: 'POST only' });
  }

  const origin = req.headers.origin || '';
  // Same-origin form posts always carry Origin on fetch; block cross-site use.
  if (origin && !ALLOWED_ORIGINS.has(origin) && !origin.startsWith('http://localhost') && !origin.includes('vercel.app')) {
    return res.status(403).json({ error: 'forbidden origin' });
  }

  const email = (req.body && req.body.email ? String(req.body.email) : '').trim().slice(0, 254);
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email)) {
    return res.status(400).json({ error: 'invalid email' });
  }

  const r = await fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${process.env.RESEND_API_KEY}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      from: 'sidetap waitlist <sidetap@practicalsystems.io>',
      to: ['wes@practicalsystems.io'],
      subject: `sidetap Pro waitlist: ${email}`,
      text: `${email} joined the sidetap Pro waitlist.\n\ntime: ${new Date().toISOString()}\nua: ${req.headers['user-agent'] || 'unknown'}`,
    }),
  });

  if (!r.ok) {
    const detail = await r.text().catch(() => '');
    console.error('resend failed', r.status, detail);
    return res.status(502).json({ error: 'delivery failed' });
  }

  return res.status(200).json({ ok: true });
};
