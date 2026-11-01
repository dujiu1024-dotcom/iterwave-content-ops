import { useEffect, useState } from "react";
import { api, Platform, AccountInfo, LoginStatus, RegisteredPlatformAccount } from "../api";

const PLATFORM_EMOJI: Record<Platform, string> = {
  xhs: "小", douyin: "抖", kuaishou: "快", wechat_channels: "视",
};

const LOGIN_STATE_LABEL: Record<string, { label: string; color: string }> = {
  starting:       { label: "启动浏览器...", color: "gray" },
  qr_ready:       { label: "等待扫码",    color: "blue" },
  window_login:   { label: "等待扫码",    color: "blue" },  // Easel headed 模式
  scanned:        { label: "已扫描，等待跳转", color: "orange" },
  sms_required:   { label: "需短信验证",  color: "orange" },
  verifying:      { label: "保存登录态",  color: "blue" },
  success:        { label: "登录成功 ✓",  color: "green" },
  expired:        { label: "二维码过期",  color: "gray" },
  error:          { label: "登录失败 ✕",  color: "red" },
  unknown:        { label: "未知",        color: "gray" },
};

const ACCOUNT_STATUS_LABEL: Record<string, { label: string; color: string }> = {
  logged_in:     { label: "✓ 已登录",      color: "green" },
  logging_in:    { label: "▣ 登录中...",  color: "blue" },
  not_logged_in: { label: "✕ 未登录",      color: "gray" },
};

export default function Accounts() {
  const [accounts, setAccounts] = useState<AccountInfo[] | null>(null);
  const [registeredAccounts, setRegisteredAccounts] = useState<RegisteredPlatformAccount[]>([]);
  const [identityDrafts, setIdentityDrafts] = useState<Record<string, { account_handle: string; display_name: string; notes: string }>>({});
  const [savingIdentity, setSavingIdentity] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [activeLogin, setActiveLogin] = useState<Platform | null>(null);
  const [loginStatus, setLoginStatus] = useState<LoginStatus | null>(null);
  const [smsCode, setSmsCode] = useState("");
  const [smsSubmitting, setSmsSubmitting] = useState(false);
  const [editingPlatform, setEditingPlatform] = useState<Platform | null>(null);
  const [editHandle, setEditHandle] = useState("");
  const [editDisplayName, setEditDisplayName] = useState("");
  const [profileSaving, setProfileSaving] = useState(false);

  const load = async () => {
    setErr(null);
    try {
      const [r, registered] = await Promise.all([api.accounts(), api.platformAccounts()]);
      setAccounts(r.accounts);
      setRegisteredAccounts(registered.items ?? []);
      setIdentityDrafts(Object.fromEntries(
        (registered.items ?? []).map((item) => [item.platform, {
          account_handle: item.account_handle ?? "",
          display_name: item.display_name ?? "",
          notes: item.notes ?? "",
        }]),
      ));
    } catch (e: any) { setErr(e.message); }
  };

  useEffect(() => { load(); }, []);

  // 轮询登录状态（2s 一次）。只有 success / expired / error 是终态——
  // unknown 只会出现在 runner 刚启动、状态文件还没写出时，不能当终态停止轮询（否则二维码永远出不来）
  useEffect(() => {
    if (!activeLogin) return;
    let stop = false;
    const deadline = Date.now() + 300_000;
    const t = setInterval(async () => {
      if (Date.now() > deadline) { clearInterval(t); return; }
      try {
        const st = await api.loginStatus(activeLogin);
        setLoginStatus(st);
        if (["success", "expired", "error"].includes(st.state)) {
          // 结束状态：再等 1s 后关弹窗
          setTimeout(() => {
            if (st.state === "success") {
              setActiveLogin(null);
              load();
            } else {
              // 失败/过期：保持弹窗让用户点"关闭"或"重试"
            }
          }, 1500);
          stop = true;
        }
      } catch (e) { /* ignore */ }
      if (stop) clearInterval(t);
    }, 2000);
    return () => clearInterval(t);
  }, [activeLogin]);

  const handleLogin = async (p: Platform) => {
    setActiveLogin(p);
    setLoginStatus(null);
    try {
      // 重新获取二维码 / 重试时必须重启 runner，否则后端会直接返回旧进程的状态
      const st = await api.startLogin(p, true);
      setLoginStatus(st);
    } catch (e: any) {
      setLoginStatus({ platform: p, platform_display: PLATFORM_EMOJI[p], state: "error", message: e.message, ts: Date.now() });
    }
  };

  const handleSubmitSms = async () => {
    if (!activeLogin) return;
    setSmsSubmitting(true);
    try {
      await api.submitLoginSms(activeLogin, smsCode);
      setSmsCode("");
    } catch (e: any) {
      alert("验证码提交失败：" + e.message);
    } finally {
      setSmsSubmitting(false);
    }
  };

  const handleLogout = async (p: Platform) => {
    if (!confirm(`确定要退出 ${PLATFORM_EMOJI[p]} 的登录态吗？本地登录信息将被删除，下次存草稿需要重新扫码。`)) return;
    try {
      await api.logout(p);
      load();
    } catch (e: any) { alert("退出失败：" + e.message); }
  };

  const saveIdentity = async (platform: Platform) => {
    const draft = identityDrafts[platform] ?? { account_handle: "", display_name: "", notes: "" };
    if (!draft.account_handle.trim()) {
      alert("请填写平台账号标识，避免无法区分账号");
      return;
    }
    const existing = registeredAccounts.find((item) => item.platform === platform);
    setSavingIdentity(platform);
    try {
      const saved = await api.savePlatformAccount(existing?.id ?? null, {
        platform,
        account_handle: draft.account_handle.trim(),
        display_name: draft.display_name.trim(),
        notes: draft.notes.trim(),
        status: existing?.status ?? "active",
      });
      setRegisteredAccounts((prev) => [...prev.filter((item) => item.platform !== platform), saved]);
      setIdentityDrafts((prev) => ({ ...prev, [platform]: {
        account_handle: saved.account_handle,
        display_name: saved.display_name,
        notes: saved.notes,
      } }));
    } catch (e: any) {
      alert("保存账号身份失败：" + e.message);
    } finally {
      setSavingIdentity(null);
    }
  };

  const deleteIdentity = async (platform: Platform) => {
    const existing = registeredAccounts.find((item) => item.platform === platform);
    if (!existing || !confirm("确定清除这条账号身份登记吗？不会退出平台登录态。")) return;
    try {
      await api.deletePlatformAccount(existing.id);
      setRegisteredAccounts((prev) => prev.filter((item) => item.id !== existing.id));
      setIdentityDrafts((prev) => ({ ...prev, [platform]: { account_handle: "", display_name: "", notes: "" } }));
    } catch (e: any) {
      alert("清除账号身份失败：" + e.message);
    }
  };

  const activeLoginProfile = activeLogin
    ? registeredAccounts.find((item) => item.platform === activeLogin)
    : undefined;

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">账号与登录</div>
          <h1 className="page-title">账号与登录</h1>
          <div className="page-desc">
            各平台可以登录不同定位的账号；在本页用手机 App 扫码登录，退出会清除本机保存的登录信息。账号定位在「目标与策略 → 账号定位与画像」设置。
          </div>
        </div>
      </div>

      {err && <div className="note" style={{ color: "var(--red)" }}>加载失败：{err}</div>}

      {/* 四平台账号状态卡 */}
      <div className="grid two" style={{ marginBottom: 20 }}>
        {(accounts || []).map((a) => {
          const st = ACCOUNT_STATUS_LABEL[a.status] || ACCOUNT_STATUS_LABEL.not_logged_in;
          const loginSt = a.login_proc_state ? LOGIN_STATE_LABEL[a.login_proc_state] : null;
          const registered = registeredAccounts.find((item) => item.platform === a.platform);
          return (
            <div key={a.platform} className="panel" style={{ padding: 18 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span style={{ fontSize: 28 }}>{PLATFORM_EMOJI[a.platform as Platform]}</span>
                  <div>
                    <div style={{ fontWeight: 700, fontSize: 15 }}>{a.platform_display}</div>
                    <div style={{ fontSize: 11, color: "var(--muted)" }}>本机登录 · 昵称为手动登记的识别信息</div>
                    <div style={{ marginTop: 4 }}>
                      <div style={{ fontSize: 13, fontWeight: 700 }}>{registered?.display_name || registered?.account_handle || "尚未登记账号昵称"}</div>
                      {registered?.account_handle && <div style={{ fontSize: 10, color: "var(--muted)" }}>账号标识：{registered.account_handle}</div>}
                    </div>
                  </div>
                </div>
                <span className={`tag ${st.color}`}>{st.label}</span>
              </div>

              {/* 登录进程状态 */}
              {loginSt && a.status === "logging_in" && (
                <div style={{ marginBottom: 10 }}>
                  <span className={`tag ${loginSt.color}`} style={{ marginRight: 8 }}>▣ {loginSt.label}</span>
                  {a.login_proc_message && <span style={{ fontSize: 11, color: "var(--muted)" }}>{a.login_proc_message}</span>}
                </div>
              )}

              {/* 已登录态信息 */}
              {a.status === "logged_in" && (
                <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 10 }}>
                  ✓ 登录有效 · 可用于存草稿、运营与数据采集
                </div>
              )}

              {/* 账号身份备注：由运营者登记，用于区分同平台账号；不冒充平台自动采集昵称 */}
              {(() => {
                const draft = identityDrafts[a.platform] ?? { account_handle: "", display_name: "", notes: "" };
                const existing = registeredAccounts.find((item) => item.platform === a.platform);
                return (
                  <div style={{ display: "grid", gap: 7, marginBottom: 12, padding: 10, border: "1px solid var(--line)", borderRadius: 8 }}>
                    <div style={{ fontSize: 11, color: "var(--muted)" }}>账号身份备注（手动登记，用于避免选错账号）</div>
                    <input className="field" value={draft.display_name} placeholder="账号昵称 / 备注名"
                      onChange={(e) => setIdentityDrafts((prev) => ({ ...prev, [a.platform]: { ...draft, display_name: e.target.value } }))} />
                    <input className="field" value={draft.account_handle} placeholder="平台账号标识（ID / 主页号）"
                      onChange={(e) => setIdentityDrafts((prev) => ({ ...prev, [a.platform]: { ...draft, account_handle: e.target.value } }))} />
                    <input className="field" value={draft.notes} placeholder="备注（可选）"
                      onChange={(e) => setIdentityDrafts((prev) => ({ ...prev, [a.platform]: { ...draft, notes: e.target.value } }))} />
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                      <button className="btn sm" disabled={savingIdentity === a.platform} onClick={() => void saveIdentity(a.platform as Platform)}>
                        {savingIdentity === a.platform ? "保存中…" : existing ? "更新身份备注" : "登记账号身份"}
                      </button>
                      {existing && <button className="btn sm danger" onClick={() => void deleteIdentity(a.platform as Platform)}>清除登记</button>}
                    </div>
                  </div>
                );
              })()}

              {/* 操作按钮 */}
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                {a.status === "not_logged_in" && (
                  <button className="btn primary" onClick={() => handleLogin(a.platform as Platform)}>▣ 扫码登录</button>
                )}
                {a.status === "logged_in" && (
                  <button className="btn" onClick={() => handleLogin(a.platform as Platform)}>↻ 重新扫码</button>
                )}
                {a.status === "logging_in" && (
                  <button className="btn" onClick={() => { api.cancelLogin(a.platform as Platform).then(load); setActiveLogin(null); }}>
                    ✕ 取消登录
                  </button>
                )}
                {a.status === "logged_in" && (
                  <button className="btn danger" onClick={() => handleLogout(a.platform as Platform)}>✕ 退出登录</button>
                )}
              </div>
              <div style={{ marginTop: 10 }}>
                {editingPlatform === a.platform ? (
                  <div className="account-profile-editor">
                    <label className="form-label">账号昵称<input className="field" value={editDisplayName} onChange={(e) => setEditDisplayName(e.target.value)} placeholder="填写平台展示昵称" /></label>
                    <label className="form-label">账号标识<input className="field" value={editHandle} onChange={(e) => setEditHandle(e.target.value)} placeholder="平台号 / 主页 ID / 账号标识" /></label>
                    <div className="flex wrap" style={{ gap: 6 }}>
                      <button className="btn sm primary" type="button" disabled={profileSaving || !editHandle.trim() || !editDisplayName.trim()} onClick={async () => {
                        setProfileSaving(true);
                        try {
                          await api.savePlatformAccount(registered?.id ?? null, {
                            platform: a.platform as Platform, account_handle: editHandle.trim(),
                            display_name: editDisplayName.trim(), status: "active", notes: registered?.notes ?? "",
                          });
                          const refreshed = await api.platformAccounts();
                          setRegisteredAccounts(refreshed.items ?? []);
                          setEditingPlatform(null);
                        } catch (e) { alert("账号资料保存失败：" + (e as Error).message); }
                        finally { setProfileSaving(false); }
                      }}>{profileSaving ? "保存中…" : "保存资料"}</button>
                      <button className="btn sm" type="button" onClick={() => setEditingPlatform(null)}>取消</button>
                    </div>
                  </div>
                ) : (
                  <button className="btn xs" type="button" onClick={() => {
                    setEditingPlatform(a.platform as Platform);
                    setEditDisplayName(registered?.display_name ?? "");
                    setEditHandle(registered?.account_handle ?? "");
                  }}>{registered ? "编辑账号资料" : "登记账号昵称"}</button>
                )}
              </div>
            </div>
          );
        })}
        {accounts && accounts.length === 0 && (
          <div className="panel" style={{ gridColumn: "1/-1" }}>
            <div className="empty">暂无账号数据</div>
          </div>
        )}
      </div>

      {/* 说明卡 */}
      <div className="panel" style={{ padding: 14, fontSize: 12, color: "var(--muted)" }}>
        <strong style={{ color: "var(--text)" }}>扫码登录流程</strong>
        <br />
        1. 点「扫码登录」，本机会弹出平台登录页并显示二维码
        <br />
        2. 用手机 App 扫码，登录成功后自动保存登录信息
        <br />
        3. 之后存草稿、运营和数据采集都会自动使用该登录，不再弹登录页
        <br />
        <br />
        <strong style={{ color: "var(--orange)" }}>注意</strong>
        <br />
        · 登录一般 1–3 个月过期，过期后存草稿会失败，点「重新扫码」即可
        <br />
        · 平台登录页改版时二维码可能识别失败，请联系维护者更新
        <br />
        · 登录信息只保存在本机，不会上传或显示在页面上
      </div>

      {/* —— 扫码登录弹窗 —— */}
      {activeLogin && (
        <div style={{
          position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)", zIndex: 9999,
          display: "flex", alignItems: "center", justifyContent: "center",
        }} onClick={() => {
          if (loginStatus && ["success", "expired", "error"].includes(loginStatus.state)) {
            setActiveLogin(null);
            load();
          }
        }}>
          <div
            style={{
              background: "#fff", borderRadius: 14, padding: 28, minWidth: 380, maxWidth: 440,
              boxShadow: "0 20px 60px rgba(0,0,0,0.3)", textAlign: "center",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>
              {PLATFORM_EMOJI[activeLogin]} {loginStatus?.platform_display || "登录中"}
            </div>
            <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 8 }}>
              {activeLoginProfile?.display_name || activeLoginProfile?.account_handle
                ? `本次登录目标：${activeLoginProfile.display_name || activeLoginProfile.account_handle}`
                : "尚未登记该平台账号昵称，请登录后补充登记，避免多个账号混淆"}
            </div>
            {activeLoginProfile?.account_handle && (
              <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 10 }}>
                账号标识：{activeLoginProfile.account_handle}
              </div>
            )}
            <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 16 }}>
              {loginStatus?.message || "准备中..."}
            </div>

            {/* QR 图片 */}
            {loginStatus?.qr_base64 && (
              <div style={{
                padding: 14, border: "1px solid var(--border)", borderRadius: 10,
                background: "#fff", display: "inline-block", marginBottom: 14,
              }}>
                <img src={loginStatus.qr_base64} alt="扫码登录 QR"
                  style={{ width: 220, height: 220, objectFit: "contain" }} />
              </div>
            )}
            {!loginStatus?.qr_base64 && loginStatus?.state === "starting" && (
              <div style={{
                width: 220, height: 220, border: "2px dashed var(--border)", borderRadius: 10,
                margin: "0 auto 14px", display: "flex", alignItems: "center", justifyContent: "center",
                color: "var(--muted)", fontSize: 12,
              }}>
                <div style={{ textAlign: "center" }}>
                  <div className="spinner" style={{ margin: "0 auto 8px" }} />
                  正在启动浏览器…
                  <br />请稍候
                </div>
              </div>
            )}

            {/* 状态指示 */}
            {loginStatus && (
              <div style={{ marginBottom: 14 }}>
                <span className={`tag ${LOGIN_STATE_LABEL[loginStatus.state]?.color || "gray"}`}>
                  {LOGIN_STATE_LABEL[loginStatus.state]?.label || loginStatus.state}
                </span>
                {loginStatus.state === "qr_ready" && (
                  <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 6 }}>
                    用 {loginStatus.platform_display} App 扫描上方二维码（有效期约几分钟，过期会自动刷新）
                  </div>
                )}
                {loginStatus.state === "sms_required" && (
                  <form
                    style={{ display: "flex", gap: 6, justifyContent: "center", marginTop: 10 }}
                    onSubmit={(e) => { e.preventDefault(); handleSubmitSms(); }}
                  >
                    <label className="sr-only" htmlFor="sms-code">短信验证码</label>
                    <input
                      id="sms-code" className="field" inputMode="numeric" autoComplete="one-time-code"
                      placeholder="手机收到的验证码" value={smsCode} maxLength={8}
                      onChange={(e) => setSmsCode(e.target.value.replace(/\D/g, ""))}
                      style={{ width: 160 }}
                    />
                    <button type="submit" className="btn sm primary" disabled={smsSubmitting || smsCode.length < 4}>
                      {smsSubmitting ? "提交中…" : "提交"}
                    </button>
                  </form>
                )}
                {loginStatus.state === "expired" && (
                  <button className="btn sm primary" style={{ marginTop: 10 }} onClick={() => handleLogin(activeLogin)}>↻ 刷新二维码</button>
                )}
                {loginStatus.state === "error" && (
                  <button className="btn sm primary" style={{ marginTop: 10 }} onClick={() => handleLogin(activeLogin)}>↻ 重试</button>
                )}
                {loginStatus.state === "success" && (
                  <div className="tag green" style={{ marginTop: 6 }}>登录成功，登录态已保存</div>
                )}
              </div>
            )}

            {/* 关闭按钮 */}
            <div style={{ display: "flex", gap: 8, justifyContent: "center" }}>
              <button
                className="btn sm"
                onClick={() => {
                  api.cancelLogin(activeLogin).catch(() => {});
                  setActiveLogin(null); load();
                }}
              >关闭</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
