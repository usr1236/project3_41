import { useEffect, useMemo, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

const ROLE_HOME = {
  ADMIN: "/admin",
  DOCTOR: "/doctor",
  PATIENT: "/patient",
  CAREGIVER: "/relative",
  SIMULATOR: "/simulator",
};

function parseJwt(token) {
  try {
    const payload = token.split(".")[1];
    return JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/")));
  } catch {
    return {};
  }
}

async function api(path, { method = "GET", token, body } = {}) {
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || JSON.stringify(data));
  return data;
}

async function tokenRequest(username, password) {
  const form = new URLSearchParams();
  form.append("username", username);
  form.append("password", password);
  const res = await fetch("/auth/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: form,
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || "Login failed");
  return data;
}

function formatTimeLabel(isoTime) {
  const dt = new Date(isoTime);
  return dt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function VitalLineChart({ points, dataKey, label, unit, color }) {
  if (!points?.length) {
    return (
      <div className="chart-shell">
        <h4>{label}</h4>
        <p className="muted">No data points yet.</p>
      </div>
    );
  }

  const width = 520;
  const height = 170;
  const left = 44;
  const right = 16;
  const top = 20;
  const bottom = 32;
  const values = points.map((point) => Number(point[dataKey])).filter((v) => Number.isFinite(v));
  const minRaw = Math.min(...values);
  const maxRaw = Math.max(...values);
  const min = minRaw === maxRaw ? minRaw - 1 : minRaw;
  const max = minRaw === maxRaw ? maxRaw + 1 : maxRaw;
  const x = (idx) => left + (idx * (width - left - right)) / Math.max(points.length - 1, 1);
  const y = (val) => top + ((max - val) * (height - top - bottom)) / Math.max(max - min, 1);
  const path = points.map((point, idx) => `${x(idx)},${y(Number(point[dataKey]))}`).join(" ");
  const startLabel = formatTimeLabel(points[0].ts);
  const endLabel = formatTimeLabel(points[points.length - 1].ts);
  const latestValue = Number(points[points.length - 1][dataKey]);

  return (
    <div className="chart-shell">
      <div className="chart-title-row">
        <h4>{label}</h4>
        <span className="badge">
          Latest: {latestValue.toFixed(1)} {unit}
        </span>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} className="chart">
        <line x1={left} y1={top} x2={left} y2={height - bottom} className="axis" />
        <line x1={left} y1={height - bottom} x2={width - right} y2={height - bottom} className="axis" />
        <text x={4} y={top + 4} className="axis-label">
          {max.toFixed(1)}
        </text>
        <text x={4} y={height - bottom + 4} className="axis-label">
          {min.toFixed(1)}
        </text>
        <text x={left} y={height - 8} className="axis-label">
          {startLabel}
        </text>
        <text x={width - right - 64} y={height - 8} className="axis-label">
          {endLabel}
        </text>
        <polyline fill="none" stroke={color} strokeWidth="2.6" points={path} />
      </svg>
      <p className="muted">
        Y-axis: {label} ({unit}) | X-axis: time
      </p>
    </div>
  );
}

function RequireRole({ role, allowed, children }) {
  if (!role) return <Navigate to="/" replace />;
  if (!allowed.includes(role)) return <Navigate to={ROLE_HOME[role] || "/"} replace />;
  return children;
}

function AuthPage({ onLogin, signup, setSignup, onSignup, authError }) {
  const [login, setLogin] = useState({ username: "doctor1", password: "doctor123" });
  return (
    <div className="auth-page">
      <div className="auth-card">
        <h1>VitalTrack</h1>
        <p className="muted">Smart remote patient monitoring with role-based access.</p>
        <h3>Secure Login</h3>
        <input
          placeholder="Username"
          value={login.username}
          onChange={(e) => setLogin({ ...login, username: e.target.value })}
        />
        <input
          type="password"
          placeholder="Password"
          value={login.password}
          onChange={(e) => setLogin({ ...login, password: e.target.value })}
        />
        <button onClick={() => onLogin(login)}>Login</button>
      </div>

      <div className="auth-card">
        <h3>Create Account</h3>
        <p className="muted">Patients, doctors, and relatives can sign up.</p>
        <input
          placeholder="Username"
          value={signup.username}
          onChange={(e) => setSignup({ ...signup, username: e.target.value })}
        />
        <input
          type="password"
          placeholder="Password"
          value={signup.password}
          onChange={(e) => setSignup({ ...signup, password: e.target.value })}
        />
        <select value={signup.role} onChange={(e) => setSignup({ ...signup, role: e.target.value })}>
          <option value="PATIENT">PATIENT</option>
          <option value="DOCTOR">DOCTOR</option>
          <option value="CAREGIVER">RELATIVE (CAREGIVER)</option>
        </select>
        <input
          placeholder="Full name (required for patient)"
          value={signup.full_name}
          onChange={(e) => setSignup({ ...signup, full_name: e.target.value })}
        />
        {signup.role === "CAREGIVER" && (
          <input
            type="number"
            placeholder="Patient ID for admin approval"
            value={signup.patient_id}
            onChange={(e) => setSignup({ ...signup, patient_id: Number(e.target.value) })}
          />
        )}
        <button onClick={onSignup}>Sign Up</button>
      </div>

      {authError ? <p className="error-banner">{authError}</p> : null}
    </div>
  );
}

function HeaderBar({ role, wsStatus, onLogout }) {
  const location = useLocation();
  return (
    <header className="topbar">
      <div>
        <h2>{role} Portal</h2>
        <p className="muted">Path: {location.pathname}</p>
      </div>
      <div className="topbar-actions">
        {["DOCTOR", "ADMIN"].includes(role) && <span className="badge">WebSocket: {wsStatus}</span>}
        <button onClick={onLogout}>Logout</button>
      </div>
    </header>
  );
}

function StatCard({ label, value }) {
  return (
    <div className="stat-card">
      <span className="stat-label">{label}</span>
      <strong>{value ?? "-"}</strong>
    </div>
  );
}

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const [token, setToken] = useState(localStorage.getItem("vt_token") || "");
  const [authError, setAuthError] = useState("");
  const role = useMemo(() => (token ? parseJwt(token).role : ""), [token]);

  const [signup, setSignup] = useState({
    username: "",
    password: "",
    role: "PATIENT",
    full_name: "",
    patient_id: 1,
  });

  const [output, setOutputState] = useState("Ready");
  const [wsStatus, setWsStatus] = useState("disconnected");
  const [wsSeed, setWsSeed] = useState(0);
  const [events, setEvents] = useState([]);

  const [patientId, setPatientId] = useState(1);
  const [patientList, setPatientList] = useState([]);
  const [vitals, setVitals] = useState({
    heart_rate: 90,
    spo2: 97,
    bp_sys: 120,
    bp_dia: 80,
    temperature: 36.9,
    source: "manual-ui",
  });
  const [autoMode, setAutoMode] = useState(false);
  const [autoIntervalMs, setAutoIntervalMs] = useState(2000);
  const [autoScope, setAutoScope] = useState("ONE");
  const [autoSomePatientIds, setAutoSomePatientIds] = useState("1");
  const [points, setPoints] = useState([]);
  const [stats, setStats] = useState({});
  const [systemMetrics, setSystemMetrics] = useState(null);
  const [dashboard, setDashboard] = useState(null);
  const [patientPortal, setPatientPortal] = useState(null);
  const [relativeDash, setRelativeDash] = useState(null);
  const [caregiverRequests, setCaregiverRequests] = useState([]);
  const [notifications, setNotifications] = useState([]);
  const [chatInput, setChatInput] = useState("");
  const [chatPatientId, setChatPatientId] = useState(1);
  const [chatHistory, setChatHistory] = useState([]);

  const canIngest = ["DOCTOR", "ADMIN", "SIMULATOR"].includes(role);

  const parseSomeIds = () =>
    autoSomePatientIds
      .split(",")
      .map((v) => Number(v.trim()))
      .filter((v, idx, arr) => Number.isInteger(v) && v > 0 && arr.indexOf(v) === idx);

  const resolveAutoPatientIds = () => {
    if (autoScope === "ALL") {
      return patientList.map((p) => p.id);
    }
    if (autoScope === "SOME") {
      return parseSomeIds();
    }
    return [patientId];
  };

  useEffect(() => {
    if (!role) return;
    const target = ROLE_HOME[role] || "/";
    if (location.pathname === "/") navigate(target, { replace: true });
  }, [role, navigate, location.pathname]);

  useEffect(() => {
    if (!token || !["DOCTOR", "ADMIN"].includes(role)) {
      setWsStatus("disconnected");
      return undefined;
    }
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${window.location.host}/ws/doctor?token=${encodeURIComponent(token)}`);
    setWsStatus("connecting");
    ws.onopen = () => {
      setWsStatus("connected");
      ws.send("connected");
    };
    ws.onmessage = (e) => setEvents((prev) => [e.data, ...prev].slice(0, 120));
    ws.onerror = () => setWsStatus("error");
    ws.onclose = () => setWsStatus("disconnected");
    return () => ws.close();
  }, [token, role, wsSeed]);

  useEffect(() => {
    if (!token || !["ADMIN", "DOCTOR", "SIMULATOR"].includes(role)) return;
    loadPatientList().catch(() => {});
  }, [token, role]);

  useEffect(() => {
    if (!autoMode || !canIngest || !token) return undefined;
    const targetPatientIds = resolveAutoPatientIds();
    if (!targetPatientIds.length) {
      setOutputState("No patient IDs selected for auto mode.");
      setAutoMode(false);
      return undefined;
    }
    const tick = async () => {
      try {
        const jobs = targetPatientIds.map((targetId) =>
          api("/v1/vitals", {
            method: "POST",
            token,
            body: {
              patient_id: targetId,
              heart_rate: Math.round(65 + Math.random() * 85),
              spo2: Number((84 + Math.random() * 15).toFixed(1)),
              bp_sys: Math.round(100 + Math.random() * 78),
              bp_dia: Math.round(60 + Math.random() * 44),
              temperature: Number((36.1 + Math.random() * 3.2).toFixed(1)),
              source: "auto-mode",
            },
          })
        );
        await Promise.allSettled(jobs);
        setOutputState(`Auto stream active for patient IDs: ${targetPatientIds.join(", ")}`);
      } catch (e) {
        setOutputState(`Auto mode failed: ${String(e)}`);
      }
    };
    tick();
    const id = window.setInterval(tick, Math.max(500, autoIntervalMs));
    return () => window.clearInterval(id);
  }, [autoMode, autoIntervalMs, canIngest, token, patientId, autoScope, autoSomePatientIds, patientList]);

  const pushOutput = (data) => setOutputState(typeof data === "string" ? data : JSON.stringify(data, null, 2));

  const onLogin = async (credentials) => {
    try {
      const data = await tokenRequest(credentials.username, credentials.password);
      localStorage.setItem("vt_token", data.access_token);
      setToken(data.access_token);
      setAuthError("");
      const nextRole = parseJwt(data.access_token).role;
      navigate(ROLE_HOME[nextRole] || "/", { replace: true });
      pushOutput(`Login successful as ${nextRole}`);
    } catch (e) {
      setAuthError(String(e));
    }
  };

  const onSignup = async () => {
    try {
      const body = {
        username: signup.username,
        password: signup.password,
        role: signup.role,
        full_name: signup.full_name || null,
        patient_id: signup.role === "CAREGIVER" ? Number(signup.patient_id) : null,
      };
      const data = await api("/auth/register", { method: "POST", body });
      setAuthError("");
      pushOutput(data);
    } catch (e) {
      setAuthError(String(e));
    }
  };

  const logout = () => {
    localStorage.removeItem("vt_token");
    setToken("");
    setAutoMode(false);
    navigate("/", { replace: true });
  };

  const loadPatientList = async () => {
    const data = await api("/v1/admin/patients", { token });
    setPatientList(data || []);
    if (data?.length && !data.some((p) => p.id === patientId)) {
      setPatientId(data[0].id);
    }
    return data;
  };

  const loadSystemMetrics = async () => {
    const data = await api("/v1/system/metrics", { token });
    setSystemMetrics(data);
    pushOutput(data);
  };

  const loadPatientInsights = async (targetPatientId = patientId) => {
    const [series, summary] = await Promise.all([
      api(`/v1/patients/${targetPatientId}/vitals?limit=120`, { token }),
      api(`/v1/patients/${targetPatientId}/stats?limit=120`, { token }),
    ]);
    setPoints(series.points || []);
    setStats(summary.summary || {});
    setPatientId(targetPatientId);
  };

  const sendOneReading = async () => {
    const body = { ...vitals, patient_id: patientId };
    const data = await api("/v1/vitals", { method: "POST", token, body });
    pushOutput(data);
  };

  const loadDoctorDashboard = async () => {
    const data = await api("/v1/doctor/dashboard", { token });
    setDashboard(data);
    pushOutput({ open_alerts: data.total_open_alerts });
  };

  const loadPatientPortal = async () => {
    const data = await api("/v1/patient/portal", { token });
    setPatientPortal(data);
    setPatientId(data.patient_id);
    await loadPatientInsights(data.patient_id);
  };

  const loadRelativeDash = async () => {
    const data = await api("/v1/caregiver/dashboard", { token });
    setRelativeDash(data);
    if (data.assigned_patients?.length) {
      await loadPatientInsights(data.assigned_patients[0].patient_id);
    }
  };

  const loadRequests = async () => {
    const data = await api("/v1/admin/caregiver-requests", { token });
    setCaregiverRequests(data);
  };

  const loadNotifications = async () => {
    const data = await api("/v1/notifications?limit=120", { token });
    setNotifications(data || []);
    pushOutput({ notifications_loaded: data?.length || 0 });
  };

  const sendChatMessage = async () => {
    if (!chatInput.trim()) return;
    try {
      const body = {
        message: chatInput.trim(),
        patient_id: Number(chatPatientId) || null,
      };
      const data = await api("/v1/chatbot/message", { method: "POST", token, body });
      setChatHistory((prev) =>
        [
          ...prev,
          {
            user: chatInput.trim(),
            reply: data.reply,
            risk_level: data.risk_level,
            ts: new Date().toISOString(),
          },
        ].slice(-30)
      );
      setChatInput("");
      pushOutput(data);
    } catch (e) {
      pushOutput(String(e));
    }
  };

  const reviewRequest = async (requestId, approve) => {
    const path = `/v1/admin/caregiver-requests/${requestId}/${approve ? "approve" : "reject"}`;
    await api(path, { method: "POST", token, body: { notes: approve ? "Approved in portal" : "Rejected in portal" } });
    await loadRequests();
  };

  if (!token) {
    return <AuthPage onLogin={onLogin} signup={signup} setSignup={setSignup} onSignup={onSignup} authError={authError} />;
  }

  return (
    <div className="app-shell">
      <HeaderBar role={role} wsStatus={wsStatus} onLogout={logout} />

      <nav className="portal-nav">
        {role === "ADMIN" && <button onClick={() => navigate("/admin")}>Admin Home</button>}
        {role === "DOCTOR" && <button onClick={() => navigate("/doctor")}>Doctor Home</button>}
        {role === "PATIENT" && <button onClick={() => navigate("/patient")}>Patient Home</button>}
        {role === "CAREGIVER" && <button onClick={() => navigate("/relative")}>Relative Home</button>}
        {role === "SIMULATOR" && <button onClick={() => navigate("/simulator")}>Simulator Home</button>}
      </nav>

      <Routes>
        <Route
          path="/admin"
          element={
            <RequireRole role={role} allowed={["ADMIN"]}>
              <section className="panel-grid">
                <article className="panel">
                  <h3>Admin Control</h3>
                  <p className="muted">Review relative access requests and monitor system-wide patient trends.</p>
                  <div className="row">
                    <button onClick={loadDoctorDashboard}>Load Alerts Snapshot</button>
                    <button onClick={loadRequests}>Load Relative Requests</button>
                    <button onClick={() => loadPatientInsights(patientId)}>Load Patient Charts</button>
                    <button onClick={loadPatientList}>Refresh Patient IDs</button>
                    <button onClick={loadSystemMetrics}>Load NFR Metrics</button>
                    <button onClick={loadNotifications}>Load Notifications</button>
                  </div>
                  <label>
                    Patient ID
                    <input type="number" value={patientId} onChange={(e) => setPatientId(Number(e.target.value))} />
                  </label>
                  <div className="row">
                    <label>
                      Auto Mode Scope
                      <select value={autoScope} onChange={(e) => setAutoScope(e.target.value)}>
                        <option value="ONE">One patient (Patient ID)</option>
                        <option value="SOME">Some patient IDs (comma separated)</option>
                        <option value="ALL">All patients</option>
                      </select>
                    </label>
                    <label>
                      Some IDs (1,2,3)
                      <input
                        value={autoSomePatientIds}
                        onChange={(e) => setAutoSomePatientIds(e.target.value)}
                        placeholder="1,2,3"
                      />
                    </label>
                    <label>
                      Auto Interval (ms)
                      <input
                        type="number"
                        min="500"
                        value={autoIntervalMs}
                        onChange={(e) => setAutoIntervalMs(Number(e.target.value) || 2000)}
                      />
                    </label>
                    <button onClick={() => setAutoMode((s) => !s)}>{autoMode ? "Stop Auto Mode" : "Start Auto Mode"}</button>
                  </div>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Request ID</th>
                          <th>Relative</th>
                          <th>Patient</th>
                          <th>Status</th>
                          <th>Action</th>
                        </tr>
                      </thead>
                      <tbody>
                        {caregiverRequests.map((item) => (
                          <tr key={item.id}>
                            <td>{item.id}</td>
                            <td>{item.caregiver_username || item.caregiver_user_id}</td>
                            <td>{item.patient_id}</td>
                            <td>{item.status}</td>
                            <td>
                              {item.status === "PENDING" && (
                                <div className="row">
                                  <button onClick={() => reviewRequest(item.id, true)}>Approve</button>
                                  <button className="secondary" onClick={() => reviewRequest(item.id, false)}>
                                    Reject
                                  </button>
                                </div>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </article>
                <article className="panel">
                  <h3>Realtime Summary</h3>
                  <div className="stats-grid">
                    <StatCard label="Open Alerts" value={dashboard?.total_open_alerts} />
                    <StatCard label="Samples" value={stats.samples} />
                    <StatCard label="Avg Heart Rate" value={stats.heart_rate_avg} />
                    <StatCard label="Min SpO2" value={stats.spo2_min} />
                    <StatCard label="Ingestion RPS (1m)" value={systemMetrics?.ingestion?.readings_per_second_last_minute} />
                    <StatCard label="Outbox Pending" value={systemMetrics?.outbox?.pending} />
                    <StatCard label="Queue Messages" value={systemMetrics?.queue?.messages} />
                    <StatCard label="Outbox p95(s)" value={systemMetrics?.outbox?.publish_latency_p95_s_last_hour} />
                  </div>
                </article>
              </section>
            </RequireRole>
          }
        />

        <Route
          path="/doctor"
          element={
            <RequireRole role={role} allowed={["DOCTOR"]}>
              <section className="panel-grid">
                <article className="panel">
                  <h3>Doctor Workspace</h3>
                  <p className="muted">Manual and automatic data streaming for selected patient.</p>
                  <div className="row">
                    <label>
                      Patient ID
                      <input type="number" value={patientId} onChange={(e) => setPatientId(Number(e.target.value))} />
                    </label>
                    <label>
                      Auto Mode Scope
                      <select value={autoScope} onChange={(e) => setAutoScope(e.target.value)}>
                        <option value="ONE">One patient</option>
                        <option value="SOME">Some IDs</option>
                        <option value="ALL">All known patients</option>
                      </select>
                    </label>
                    <label>
                      Some IDs
                      <input
                        value={autoSomePatientIds}
                        onChange={(e) => setAutoSomePatientIds(e.target.value)}
                        placeholder="1,2,3"
                      />
                    </label>
                    <label>
                      Auto Interval (ms)
                      <input
                        type="number"
                        min="500"
                        value={autoIntervalMs}
                        onChange={(e) => setAutoIntervalMs(Number(e.target.value) || 2000)}
                      />
                    </label>
                  </div>
                  <div className="vitals-grid">
                    {[
                      ["heart_rate", "Heart Rate (bpm)"],
                      ["spo2", "SpO2 (%)"],
                      ["bp_sys", "BP Systolic"],
                      ["bp_dia", "BP Diastolic"],
                      ["temperature", "Temperature (C)"],
                    ].map(([key, label]) => (
                      <label key={key}>
                        {label}
                        <input
                          type="number"
                          step={key === "spo2" || key === "temperature" ? "0.1" : "1"}
                          value={vitals[key]}
                          onChange={(e) => setVitals({ ...vitals, [key]: Number(e.target.value) })}
                        />
                      </label>
                    ))}
                  </div>
                  <div className="row">
                    <button onClick={sendOneReading}>Send One Reading</button>
                    <button onClick={() => setAutoMode((s) => !s)}>{autoMode ? "Stop Auto Mode" : "Start Auto Mode"}</button>
                    <button onClick={loadDoctorDashboard}>Load Alert Dashboard</button>
                    <button onClick={loadPatientList}>Refresh Patient IDs</button>
                    <button onClick={() => loadPatientInsights(patientId)}>Load Charts</button>
                    <button onClick={() => setWsSeed((v) => v + 1)}>Reconnect WebSocket</button>
                    <button onClick={loadNotifications}>Load Notifications</button>
                  </div>
                </article>
                <article className="panel">
                  <h3>Doctor Metrics</h3>
                  <div className="stats-grid">
                    <StatCard label="Open Alerts" value={dashboard?.total_open_alerts} />
                    <StatCard label="Samples" value={stats.samples} />
                    <StatCard label="Max Temp" value={stats.temperature_max} />
                    <StatCard label="Latest Time" value={stats.latest_ts ? formatTimeLabel(stats.latest_ts) : "-"} />
                  </div>
                </article>
              </section>
            </RequireRole>
          }
        />

        <Route
          path="/patient"
          element={
            <RequireRole role={role} allowed={["PATIENT"]}>
              <section className="panel-grid">
                <article className="panel">
                  <h3>My Health Portal</h3>
                  <p className="muted">Your recent vitals, alerts, and trend lines.</p>
                  <div className="row">
                    <button onClick={loadPatientPortal}>Load My Portal</button>
                    <button onClick={() => loadPatientInsights(patientId)}>Refresh Charts</button>
                    <button onClick={loadNotifications}>Load Notifications</button>
                  </div>
                  <div className="stats-grid">
                    <StatCard label="Patient ID" value={patientPortal?.patient_id || patientId} />
                    <StatCard label="Samples" value={stats.samples} />
                    <StatCard label="Avg Heart Rate" value={stats.heart_rate_avg} />
                    <StatCard label="Min SpO2" value={stats.spo2_min} />
                  </div>
                </article>
              </section>
            </RequireRole>
          }
        />

        <Route
          path="/relative"
          element={
            <RequireRole role={role} allowed={["CAREGIVER"]}>
              <section className="panel-grid">
                <article className="panel">
                  <h3>Relative Dashboard</h3>
                  <p className="muted">Track assigned patients after admin approval.</p>
                  <div className="row">
                    <button onClick={loadRelativeDash}>Load Assigned Patients</button>
                    <button onClick={() => loadPatientInsights(patientId)}>Load Selected Patient Charts</button>
                    <button onClick={loadNotifications}>Load Notifications</button>
                  </div>
                  <div className="row">
                    <label>
                      Selected Patient ID
                      <input type="number" value={patientId} onChange={(e) => setPatientId(Number(e.target.value))} />
                    </label>
                  </div>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Patient ID</th>
                          <th>Name</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(relativeDash?.assigned_patients || []).map((patient) => (
                          <tr key={patient.patient_id}>
                            <td>{patient.patient_id}</td>
                            <td>{patient.full_name}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </article>
              </section>
            </RequireRole>
          }
        />

        <Route
          path="/simulator"
          element={
            <RequireRole role={role} allowed={["SIMULATOR"]}>
              <section className="panel-grid">
                <article className="panel">
                  <h3>Simulator Console</h3>
                  <p className="muted">Generate continuous synthetic vitals for demo testing.</p>
                  <div className="row">
                    <label>
                      Patient ID
                      <input type="number" value={patientId} onChange={(e) => setPatientId(Number(e.target.value))} />
                    </label>
                    <label>
                      Auto Mode Scope
                      <select value={autoScope} onChange={(e) => setAutoScope(e.target.value)}>
                        <option value="ONE">One patient</option>
                        <option value="SOME">Some IDs</option>
                        <option value="ALL">All known patients</option>
                      </select>
                    </label>
                    <label>
                      Some IDs
                      <input
                        value={autoSomePatientIds}
                        onChange={(e) => setAutoSomePatientIds(e.target.value)}
                        placeholder="1,2,3"
                      />
                    </label>
                    <label>
                      Auto Interval (ms)
                      <input
                        type="number"
                        min="500"
                        value={autoIntervalMs}
                        onChange={(e) => setAutoIntervalMs(Number(e.target.value) || 2000)}
                      />
                    </label>
                    <button onClick={() => setAutoMode((v) => !v)}>{autoMode ? "Stop Stream" : "Start Stream"}</button>
                    <button onClick={loadPatientList}>Refresh Patient IDs</button>
                    <button onClick={loadSystemMetrics}>Load NFR Metrics</button>
                    <button onClick={() => loadPatientInsights(patientId)}>Load Charts</button>
                    <button onClick={loadNotifications}>Load Notifications</button>
                  </div>
                </article>
                <article className="panel">
                  <h3>Simulator Metrics</h3>
                  <div className="stats-grid">
                    <StatCard label="Readings Last Minute" value={systemMetrics?.ingestion?.readings_last_minute} />
                    <StatCard label="Ingestion RPS" value={systemMetrics?.ingestion?.readings_per_second_last_minute} />
                    <StatCard label="Active Patients (15m)" value={systemMetrics?.ingestion?.active_patients_last_15m} />
                    <StatCard label="Outbox Pending" value={systemMetrics?.outbox?.pending} />
                    <StatCard label="Queue Messages" value={systemMetrics?.queue?.messages} />
                    <StatCard label="Queue Consumers" value={systemMetrics?.queue?.consumers} />
                  </div>
                </article>
              </section>
            </RequireRole>
          }
        />

        <Route path="*" element={<Navigate to={ROLE_HOME[role] || "/"} replace />} />
      </Routes>

      <section className="panel-grid">
        <article className="panel">
          <h3>Data Visualizations</h3>
          <VitalLineChart points={points} dataKey="heart_rate" label="Heart Rate Trend" unit="bpm" color="#2a7fff" />
          <VitalLineChart points={points} dataKey="spo2" label="SpO2 Trend" unit="%" color="#00a37a" />
          <VitalLineChart points={points} dataKey="temperature" label="Temperature Trend" unit="C" color="#ff5f5f" />
        </article>
        <article className="panel">
          <h3>Notifications</h3>
          <div className="row">
            <button onClick={loadNotifications}>Refresh Notifications</button>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Alert</th>
                  <th>Recipient</th>
                  <th>Channel</th>
                  <th>Status</th>
                  <th>Details</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {notifications.slice(0, 80).map((n) => (
                  <tr key={n.id}>
                    <td>{n.id}</td>
                    <td>{n.alert_id}</td>
                    <td>{n.recipient}</td>
                    <td>{n.channel}</td>
                    <td>{n.status}</td>
                    <td>{n.details}</td>
                    <td>{formatTimeLabel(n.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h3>Health Chatbot</h3>
          <p className="muted">Non-diagnostic triage support only. No automatic alert escalation from chatbot messages.</p>
          <div className="row">
            <label>
              Patient ID
              <input
                type="number"
                value={chatPatientId}
                onChange={(e) => setChatPatientId(Number(e.target.value) || 1)}
              />
            </label>
            <label>
              Message
              <input
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                placeholder="e.g., chest pain and breathlessness"
              />
            </label>
            <button onClick={sendChatMessage}>Send to Chatbot</button>
          </div>
          <pre>
            {chatHistory
              .map(
                (c) =>
                  `[${formatTimeLabel(c.ts)}] You: ${c.user}\nBot (${c.risk_level}): ${c.reply}`
              )
              .join("\n\n") || "No chatbot messages yet"}
          </pre>

          <h3>Realtime Events</h3>
          <pre>{events.join("\n") || "No websocket events yet"}</pre>
          <h3>System Output</h3>
          <pre>{output}</pre>
        </article>
      </section>
    </div>
  );
}
