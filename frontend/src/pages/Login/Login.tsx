import { useState, type FormEvent } from "react";
import { useAuth } from "../../auth/AuthContext";
import "./Login.css";

export default function Login() {
  const { login, register } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  function switchMode(next: "login" | "register") {
    setMode(next);
    setError("");
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (!username.trim()) return setError("请输入用户名");
    if (password.length < 1) return setError("请输入密码");
    if (mode === "register") {
      if (username.length < 3) return setError("用户名至少 3 个字符");
      if (password.length < 6) return setError("密码至少 6 位");
    }
    setBusy(true);
    try {
      if (mode === "login") {
        await login(username.trim(), password);
      } else {
        await register(username.trim(), password, displayName.trim() || undefined);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <div className="login-card">
        <div className="login-brand">
          <span className="login-logo">第二大脑</span>
          <span className="login-slogan">知识库 · 图谱 · AI 助手</span>
        </div>
        <form onSubmit={onSubmit} className="login-form">
          <div className="login-tabs">
            <button
              type="button"
              className={mode === "login" ? "active" : ""}
              onClick={() => switchMode("login")}
            >
              登录
            </button>
            <button
              type="button"
              className={mode === "register" ? "active" : ""}
              onClick={() => switchMode("register")}
            >
              注册
            </button>
          </div>

          {mode === "register" && (
            <label className="login-field">
              <span>昵称（可选）</span>
              <input
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="显示名称，缺省取用户名"
                autoComplete="nickname"
              />
            </label>
          )}
          <label className="login-field">
            <span>用户名</span>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="请输入用户名"
              autoComplete="username"
            />
          </label>
          <label className="login-field">
            <span>密码</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={mode === "register" ? "至少 6 位" : "请输入密码"}
              autoComplete={mode === "register" ? "new-password" : "current-password"}
            />
          </label>

          {error && <div className="login-error">{error}</div>}

          <button type="submit" className="login-submit" disabled={busy}>
            {busy ? "请稍候…" : mode === "login" ? "登 录" : "注 册"}
          </button>
          <p className="login-hint">
            {mode === "login"
              ? "还没有账号？"
              : "已有账号？"}
            <a
              href="#"
              onClick={(e) => {
                e.preventDefault();
                switchMode(mode === "login" ? "register" : "login");
              }}
            >
              {mode === "login" ? "立即注册" : "返回登录"}
            </a>
          </p>
        </form>
      </div>
    </div>
  );
}