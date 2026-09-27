import type { Session } from '@supabase/supabase-js';
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';

import { supabase } from './supabase';

interface Ctx {
  session: Session | null;
  // False until the first session check finishes (or immediately, without Supabase).
  ready: boolean;
  error: string | null;
}

const SessionContext = createContext<Ctx>({ session: null, ready: false, error: null });

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(!supabase);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!supabase) return;
    const client = supabase;
    let alive = true;

    (async () => {
      const { data } = await client.auth.getSession();
      let s = data.session;
      if (!s) {
        // Everyone gets an anonymous account first; they can attach an email later in the Lab.
        const res = await client.auth.signInAnonymously();
        if (res.error) setError(res.error.message);
        s = res.data.session;
      }
      if (alive) {
        setSession(s);
        setReady(true);
      }
    })().catch((e) => {
      if (alive) {
        setError(String(e?.message ?? e));
        setReady(true);
      }
    });

    const { data: sub } = client.auth.onAuthStateChange((event, s) => {
      setSession(s);
      // After signing out, start a fresh anonymous account so capturing keeps working.
      if (event === 'SIGNED_OUT') {
        setTimeout(() => {
          client.auth.signInAnonymously().then((res) => setError(res.error?.message ?? null));
        }, 0);
      }
    });
    return () => {
      alive = false;
      sub.subscription.unsubscribe();
    };
  }, []);

  return <SessionContext.Provider value={{ session, ready, error }}>{children}</SessionContext.Provider>;
}

export const useSession = () => useContext(SessionContext);
