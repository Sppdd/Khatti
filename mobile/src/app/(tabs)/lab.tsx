import { Ionicons } from '@expo/vector-icons';
import { Image } from 'expo-image';
import { router, useFocusEffect } from 'expo-router';
import { useCallback, useState, type ReactNode } from 'react';
import { KeyboardAvoidingView, Platform, ScrollView, Switch, TextInput, View } from 'react-native';
import Animated, { FadeIn, FadeInDown } from 'react-native-reanimated';

import { colors, fonts, radius, space } from '../../brand/theme';
import { Button, Card, Chip, PressableScale, Screen, SectionTitle, Txt } from '../../components/ui';
import { createKycCase, getHealth, type DocType, type Health } from '../../lib/api';
import { timeAgo } from '../../lib/format';
import { prepareImage, type PreparedImage } from '../../lib/image';
import { pickPhoto } from '../../lib/pick';
import { useSession } from '../../lib/session';
import { useSettings } from '../../lib/settings';
import { isCloud, listLogs } from '../../lib/store';
import { supabase } from '../../lib/supabase';
import type { ApiLog, KycCaseResult } from '../../lib/types';

const DOC_TYPES: { id: DocType; label: string }[] = [
  { id: 'national_id', label: 'National ID' },
  { id: 'passport', label: 'Passport' },
  { id: 'residence_card', label: 'Residence card' },
];

const DECISION_COLOR = { approve: colors.mint, human_review: colors.saffron, reject: colors.rose } as const;

export default function LabScreen() {
  const [logs, setLogs] = useState<ApiLog[]>([]);
  const refreshLogs = useCallback(() => {
    listLogs(30).then(setLogs).catch(() => {});
  }, []);
  useFocusEffect(refreshLogs);

  return (
    <Screen>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={{ padding: space(5), paddingBottom: 140, gap: space(6) }} keyboardShouldPersistTaps="handled">
          <View>
            <Txt variant="display" style={{ fontSize: 30 }}>
              API Lab
            </Txt>
            <Txt variant="label">Point Khatti at your API, test every endpoint, watch the logs.</Txt>
          </View>
          <ConnectionCard onCall={refreshLogs} />
          <BridgeLink />
          <KycCard onCall={refreshLogs} />
          <AccountCard />
          <LogCard logs={logs} onRefresh={refreshLogs} />
        </ScrollView>
      </KeyboardAvoidingView>
    </Screen>
  );
}

function BridgeLink() {
  const { settings } = useSettings();
  return (
    <PressableScale onPress={() => router.push('/bridge')} scaleTo={0.98}>
      <Card style={{ flexDirection: 'row', alignItems: 'center', gap: space(3), borderColor: colors.violet + '66' }}>
        <View style={{ width: 40, height: 40, borderRadius: 20, backgroundColor: colors.violet + '33', alignItems: 'center', justifyContent: 'center' }}>
          <Ionicons name="git-network" size={20} color={colors.violetSoft} />
        </View>
        <View style={{ flex: 1 }}>
          <Txt variant="heading" style={{ fontSize: 15 }}>
            Model bridge
          </Txt>
          <Txt variant="caption" numberOfLines={1}>
            Capture model: {settings.extractModel || 'server default'}
          </Txt>
        </View>
        <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
      </Card>
    </PressableScale>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <View style={{ gap: 6 }}>
      <Txt variant="caption">{label}</Txt>
      {children}
    </View>
  );
}

const inputStyle = {
  color: colors.text,
  fontFamily: fonts.body,
  fontSize: 15,
  backgroundColor: colors.surfaceHigh,
  borderRadius: radius.sm,
  borderWidth: 1,
  borderColor: colors.line,
  paddingHorizontal: space(3),
  paddingVertical: 10,
} as const;

function ConnectionCard({ onCall }: { onCall: () => void }) {
  const { settings, update } = useSettings();
  const [health, setHealth] = useState<{ data: Health; latencyMs: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const check = async () => {
    setBusy(true);
    setError(null);
    setHealth(null);
    try {
      setHealth(await getHealth());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      onCall();
    }
  };

  return (
    <View>
      <SectionTitle>Connection</SectionTitle>
      <Card style={{ gap: space(3) }}>
        <Field label="Khatti API URL">
          <TextInput
            value={settings.apiUrl}
            onChangeText={(apiUrl) => update({ apiUrl: apiUrl.trim() })}
            placeholder="https://your-khatti-api.example.com"
            placeholderTextColor={colors.textMute}
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="url"
            style={inputStyle}
          />
        </Field>
        <Field label="API key (optional, sent as Bearer token)">
          <TextInput
            value={settings.apiKey}
            onChangeText={(apiKey) => update({ apiKey: apiKey.trim() })}
            placeholder="KHATTI_API_KEY"
            placeholderTextColor={colors.textMute}
            autoCapitalize="none"
            autoCorrect={false}
            secureTextEntry
            style={inputStyle}
          />
        </Field>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <View style={{ flex: 1 }}>
            <Txt variant="heading" style={{ fontSize: 15 }}>
              Demo mode
            </Txt>
            <Txt variant="caption">Sample answers instead of real API calls</Txt>
          </View>
          <Switch
            value={settings.demoMode}
            onValueChange={(demoMode) => update({ demoMode })}
            trackColor={{ true: colors.violet, false: colors.line }}
            thumbColor={colors.text}
          />
        </View>
        <Button title="Check /health" variant="ghost" loading={busy} onPress={check} icon={<Ionicons name="pulse" size={18} color={colors.mint} />} />
        {health && (
          <Animated.View entering={FadeInDown} style={{ gap: 6 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: health.data.status === 'ok' ? colors.mint : colors.saffron }} />
              <Txt variant="heading" style={{ fontSize: 15 }}>
                {health.data.status} · {health.latencyMs} ms
              </Txt>
            </View>
            <Txt variant="caption">Extractor: {health.data.extractor ?? 'not configured'}</Txt>
            <Txt variant="caption">KYC readers: {health.data.readers.join(', ') || 'none'}</Txt>
            <Txt variant="caption">Router: {health.data.router ?? 'none'} · Auth: {health.data.auth ? 'on' : 'off'}</Txt>
          </Animated.View>
        )}
        {error && (
          <Txt variant="label" style={{ color: colors.rose }}>
            {error}
          </Txt>
        )}
      </Card>
    </View>
  );
}

function KycCard({ onCall }: { onCall: () => void }) {
  const [docType, setDocType] = useState<DocType>('national_id');
  const [docs, setDocs] = useState<{ image: PreparedImage; docType: DocType }[]>([]);
  const [result, setResult] = useState<{ data: KycCaseResult; latencyMs: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const add = async () => {
    const photo = await pickPhoto('library');
    if (!photo) return;
    const image = await prepareImage(photo.uri, photo.width, photo.height);
    setDocs((d) => [...d, { image, docType }]);
    setResult(null);
  };

  const run = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await createKycCase(docs));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      onCall();
    }
  };

  return (
    <View>
      <SectionTitle>KYC endpoint · /v1/kyc/cases</SectionTitle>
      <Card style={{ gap: space(3) }}>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space(2) }}>
          {DOC_TYPES.map((t) => (
            <Chip key={t.id} label={t.label} active={docType === t.id} onPress={() => setDocType(t.id)} color={colors.sky} />
          ))}
        </View>
        {docs.length > 0 && (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: space(2) }}>
            {docs.map((d, i) => (
              <Animated.View key={d.image.uri} entering={FadeIn} style={{ width: 96 }}>
                <Image source={{ uri: d.image.uri }} style={{ width: 96, height: 64, borderRadius: radius.sm }} contentFit="cover" />
                <PressableScale
                  onPress={() => setDocs((all) => all.filter((_, j) => j !== i))}
                  style={{ position: 'absolute', top: 4, right: 4, backgroundColor: colors.ink + 'CC', borderRadius: 10 }}
                >
                  <Ionicons name="close" size={16} color={colors.text} />
                </PressableScale>
                <Txt variant="caption" numberOfLines={1}>
                  {DOC_TYPES.find((t) => t.id === d.docType)?.label}
                </Txt>
              </Animated.View>
            ))}
          </ScrollView>
        )}
        <View style={{ flexDirection: 'row', gap: space(3) }}>
          <Button title="Add photo" variant="ghost" onPress={add} style={{ flex: 1 }} icon={<Ionicons name="add" size={18} color={colors.text} />} />
          <Button title="Run case" disabled={!docs.length} loading={busy} onPress={run} style={{ flex: 1 }} />
        </View>
        {result && (
          <Animated.View entering={FadeInDown} style={{ gap: space(2) }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <View
                style={{
                  paddingHorizontal: 12,
                  paddingVertical: 4,
                  borderRadius: radius.pill,
                  backgroundColor: (DECISION_COLOR[result.data.decision] ?? colors.textDim) + '26',
                }}
              >
                <Txt variant="heading" style={{ fontSize: 14, color: DECISION_COLOR[result.data.decision] ?? colors.text }}>
                  {result.data.decision.replace('_', ' ')}
                </Txt>
              </View>
              <Txt variant="label">
                {(result.data.confidence * 100).toFixed(0)}% · {result.latencyMs} ms
              </Txt>
            </View>
            {!!result.data.rationale && <Txt variant="label">{result.data.rationale}</Txt>}
            <Txt variant="mono" selectable numberOfLines={30}>
              {JSON.stringify(result.data, null, 2)}
            </Txt>
          </Animated.View>
        )}
        {error && (
          <Txt variant="label" style={{ color: colors.rose }}>
            {error}
          </Txt>
        )}
      </Card>
    </View>
  );
}

function AccountCard() {
  const { session, error: sessionError } = useSession();
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  if (!isCloud || !supabase) {
    return (
      <View>
        <SectionTitle>Storage</SectionTitle>
        <Card style={{ gap: space(2) }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <Ionicons name="phone-portrait-outline" size={18} color={colors.textDim} />
            <Txt variant="heading" style={{ fontSize: 15 }}>
              Saving on this device
            </Txt>
          </View>
          <Txt variant="caption">
            Set EXPO_PUBLIC_SUPABASE_URL and EXPO_PUBLIC_SUPABASE_ANON_KEY to sync captures, prices and logs to Supabase.
          </Txt>
        </Card>
      </View>
    );
  }
  const client = supabase;
  const user = session?.user;

  const sendCode = async () => {
    setBusy(true);
    setMsg(null);
    // An anonymous user attaches the email to the same account, so nothing captured so far is lost.
    const { error } = user?.is_anonymous
      ? await client.auth.updateUser({ email })
      : await client.auth.signInWithOtp({ email, options: { shouldCreateUser: true } });
    setBusy(false);
    if (error) setMsg(error.message);
    else setSent(true);
  };

  const verify = async () => {
    setBusy(true);
    setMsg(null);
    const { error } = await client.auth.verifyOtp({ email, token: code, type: user?.is_anonymous ? 'email_change' : 'email' });
    setBusy(false);
    if (error) setMsg(error.message);
    else {
      setSent(false);
      setCode('');
      setMsg('Email linked. Your captures follow you to any device.');
    }
  };

  return (
    <View>
      <SectionTitle>Supabase account</SectionTitle>
      <Card style={{ gap: space(3) }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <Ionicons name="cloud-done-outline" size={18} color={colors.sky} />
          <View style={{ flex: 1 }}>
            <Txt variant="heading" style={{ fontSize: 15 }}>
              {user ? (user.is_anonymous ? 'Anonymous account' : user.email) : 'Not signed in'}
            </Txt>
            {user && (
              <Txt variant="caption" numberOfLines={1}>
                {user.id}
              </Txt>
            )}
          </View>
        </View>
        {sessionError && (
          <Txt variant="caption" style={{ color: colors.rose }}>
            {sessionError} (enable Anonymous sign-ins in Supabase → Authentication → Providers)
          </Txt>
        )}
        {(!user || user.is_anonymous) &&
          (sent ? (
            <>
              <TextInput value={code} onChangeText={setCode} placeholder="6-digit code" placeholderTextColor={colors.textMute} keyboardType="number-pad" style={inputStyle} />
              <Button title="Verify code" loading={busy} disabled={code.length < 6} onPress={verify} />
            </>
          ) : (
            <>
              <TextInput
                value={email}
                onChangeText={setEmail}
                placeholder="you@example.com"
                placeholderTextColor={colors.textMute}
                autoCapitalize="none"
                keyboardType="email-address"
                style={inputStyle}
              />
              <Button title={user ? 'Save account with email' : 'Sign in with email'} variant="ghost" loading={busy} disabled={!email.includes('@')} onPress={sendCode} />
            </>
          ))}
        {user && !user.is_anonymous && <Button title="Sign out" variant="danger" onPress={() => client.auth.signOut()} />}
        {msg && <Txt variant="caption">{msg}</Txt>}
      </Card>
    </View>
  );
}

function LogCard({ logs, onRefresh }: { logs: ApiLog[]; onRefresh: () => void }) {
  return (
    <View>
      <SectionTitle
        right={
          <PressableScale onPress={onRefresh}>
            <Ionicons name="refresh" size={16} color={colors.textDim} />
          </PressableScale>
        }
      >
        Request log
      </SectionTitle>
      <Card style={{ padding: 0 }}>
        {logs.length === 0 ? (
          <Txt variant="label" style={{ padding: space(4) }}>
            No requests yet.
          </Txt>
        ) : (
          logs.map((l, i) => (
            <View key={l.id} style={{ flexDirection: 'row', alignItems: 'center', gap: space(3), padding: space(3), borderTopWidth: i ? 1 : 0, borderColor: colors.line }}>
              <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: l.ok ? colors.mint : colors.rose }} />
              <View style={{ flex: 1 }}>
                <Txt variant="label" style={{ color: colors.text }} numberOfLines={1}>
                  {l.endpoint}
                </Txt>
                {!!l.error && (
                  <Txt variant="caption" style={{ color: colors.rose }} numberOfLines={2}>
                    {l.error}
                  </Txt>
                )}
              </View>
              <View style={{ alignItems: 'flex-end' }}>
                <Txt variant="caption" style={{ color: colors.textDim }}>
                  {l.status ?? '—'} · {l.latency_ms ?? '—'} ms
                </Txt>
                <Txt variant="caption">{timeAgo(l.created_at)}</Txt>
              </View>
            </View>
          ))
        )}
      </Card>
    </View>
  );
}
