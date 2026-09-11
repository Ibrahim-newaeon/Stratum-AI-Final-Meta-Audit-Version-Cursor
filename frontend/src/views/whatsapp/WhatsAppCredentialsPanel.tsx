/**
 * Module G WhatsApp Cloud API messaging credentials.
 * Distinct from CAPI Setup WhatsApp fields (Conversions API only).
 */

import { useState } from 'react';
import {
  useDisconnectWhatsAppCredentials,
  useUpsertWhatsAppCredentials,
  useWhatsAppCredentialsStatus,
} from '@/api/whatsappCredentials';

export default function WhatsAppCredentialsPanel() {
  const { data: status, isLoading, isError } = useWhatsAppCredentialsStatus();
  const upsert = useUpsertWhatsAppCredentials();
  const disconnect = useDisconnectWhatsAppCredentials();

  const [phoneNumberId, setPhoneNumberId] = useState('');
  const [accessToken, setAccessToken] = useState('');
  const [businessAccountId, setBusinessAccountId] = useState('');
  const [displayPhone, setDisplayPhone] = useState('');
  const [message, setMessage] = useState<string | null>(null);

  const onSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setMessage(null);
    try {
      await upsert.mutateAsync({
        phone_number_id: phoneNumberId.trim(),
        access_token: accessToken.trim(),
        business_account_id: businessAccountId.trim() || undefined,
        display_phone_number: displayPhone.trim() || undefined,
      });
      setAccessToken('');
      setMessage('Messaging credentials connected.');
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Failed to save credentials');
    }
  };

  const onDisconnect = async () => {
    setMessage(null);
    try {
      await disconnect.mutateAsync();
      setMessage('Messaging credentials disconnected.');
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Failed to disconnect');
    }
  };

  return (
    <div className="space-y-6 max-w-xl">
      <div>
        <h2 className="text-lg font-semibold">Messaging credentials</h2>
        <p className="text-sm text-gray-400 mt-1">
          Per-tenant WhatsApp Cloud API for Module G outbound messaging. This is{' '}
          <strong className="text-gray-200">not</strong> the WhatsApp fields on CAPI Setup
          (those are Conversions API only).
        </p>
      </div>

      <div className="rounded-xl border border-white/10 bg-white/5 p-4 text-sm">
        {isLoading && <p className="text-gray-400">Loading status…</p>}
        {isError && <p className="text-red-400">Could not load credential status.</p>}
        {status && (
          <p className={status.connected ? 'text-emerald-400' : 'text-amber-400'}>
            {status.connected
              ? `Connected${status.display_phone_number ? ` · ${status.display_phone_number}` : ''}${
                  status.phone_number_id ? ` · ${status.phone_number_id}` : ''
                }`
              : 'Not connected — messaging fails closed until credentials are saved.'}
          </p>
        )}
      </div>

      <form onSubmit={onSave} className="space-y-3">
        <label className="block text-sm">
          <span className="text-gray-400">Phone Number ID</span>
          <input
            className="mt-1 w-full rounded-lg border border-white/10 bg-black/40 px-3 py-2"
            value={phoneNumberId}
            onChange={(e) => setPhoneNumberId(e.target.value)}
            required
            autoComplete="off"
          />
        </label>
        <label className="block text-sm">
          <span className="text-gray-400">Access token</span>
          <input
            type="password"
            className="mt-1 w-full rounded-lg border border-white/10 bg-black/40 px-3 py-2"
            value={accessToken}
            onChange={(e) => setAccessToken(e.target.value)}
            required
            autoComplete="off"
          />
        </label>
        <label className="block text-sm">
          <span className="text-gray-400">Business Account ID (optional)</span>
          <input
            className="mt-1 w-full rounded-lg border border-white/10 bg-black/40 px-3 py-2"
            value={businessAccountId}
            onChange={(e) => setBusinessAccountId(e.target.value)}
            autoComplete="off"
          />
        </label>
        <label className="block text-sm">
          <span className="text-gray-400">Display phone (optional)</span>
          <input
            className="mt-1 w-full rounded-lg border border-white/10 bg-black/40 px-3 py-2"
            value={displayPhone}
            onChange={(e) => setDisplayPhone(e.target.value)}
            autoComplete="off"
          />
        </label>
        <div className="flex gap-2 pt-2">
          <button
            type="submit"
            disabled={upsert.isPending}
            className="px-4 py-2 rounded-lg bg-[#25D366] text-black font-medium disabled:opacity-50"
          >
            {upsert.isPending ? 'Saving…' : 'Save credentials'}
          </button>
          {status?.connected && (
            <button
              type="button"
              onClick={onDisconnect}
              disabled={disconnect.isPending}
              className="px-4 py-2 rounded-lg border border-white/20 text-gray-300 disabled:opacity-50"
            >
              Disconnect
            </button>
          )}
        </div>
      </form>

      {message && <p className="text-sm text-gray-300">{message}</p>}
    </div>
  );
}
