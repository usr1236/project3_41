import { useEffect, useMemo, useRef, useState } from "react";
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

function wsBadgeClass(status) {
  if (status === "connected") return "badge ws-badge ws-connected";
  if (status === "reconnecting") return "badge ws-badge ws-connecting";
  if (status === "connecting") return "badge ws-badge ws-connecting";
  if (status === "error") return "badge ws-badge ws-error";
  return "badge ws-badge ws-disconnected";
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
          <>
            <input
              type="number"
              placeholder="Patient ID to request link (e.g., 1 or 2)"
              value={signup.patient_id}
              onChange={(e) => setSignup({ ...signup, patient_id: Number(e.target.value) })}
            />
            <p className="muted">4th field (caregiver only): enter patient ID (example: 1, 2). Admin must approve.</p>
          </>
        )}
        <button onClick={onSignup}>Sign Up</button>
      </div>

      {authError ? <p className="error-banner">{authError}</p> : null}
    </div>
  );
}

function HeaderBar({ role, wsStatus, onReconnect, onLogout }) {
  const location = useLocation();
  return (
    <header className="topbar">
      <div>
        <h2>{role} Portal</h2>
        <p className="muted">Path: {location.pathname}</p>
      </div>
      <div className="topbar-actions">
        {["DOCTOR", "ADMIN"].includes(role) && <span className={wsBadgeClass(wsStatus)}>WebSocket: {wsStatus}</span>}
        {["DOCTOR", "ADMIN"].includes(role) && <button onClick={onReconnect}>Reconnect WebSocket</button>}
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
  const [queueHealth, setQueueHealth] = useState(null);
  const [auditEntries, setAuditEntries] = useState([]);
  const [failedRetrySummary, setFailedRetrySummary] = useState(null);
  const [chatInput, setChatInput] = useState("");
  const [chatPatientId, setChatPatientId] = useState(1);
  const [chatHistory, setChatHistory] = useState([]);
  const [chatOpen, setChatOpen] = useState(false);
  const [chatUnread, setChatUnread] = useState(0);
  const [chatSending, setChatSending] = useState(false);
  const [ackNotice, setAckNotice] = useState("");
  const [ackInFlightId, setAckInFlightId] = useState(null);
  const ackNoticeTimerRef = useRef(null);
  const [chartRange, setChartRange] = useState(120);
  const [predictionRows, setPredictionRows] = useState([]);
  const [alertsPanelOpen, setAlertsPanelOpen] = useState(false);
  const [chartsPanelOpen, setChartsPanelOpen] = useState(true);
  const [predictionsPanelOpen, setPredictionsPanelOpen] = useState(true);
  const [notificationsPanelOpen, setNotificationsPanelOpen] = useState(true);
  const chartsSectionRef = useRef(null);
  const predictionsSectionRef = useRef(null);
  const notificationsSectionRef = useRef(null);
  const visiblePoints = useMemo(() => points.slice(0, chartRange), [points, chartRange]);
  const visiblePredictionRows = useMemo(() => predictionRows.slice(0, 80), [predictionRows]);
  const doctorWorkspaceTitle = useMemo(
    () => `Doctor Workspace • Patient ${patientId} • Auto ${autoMode ? "ON" : "OFF"} • WS ${wsStatus.toUpperCase()}`,
    [patientId, autoMode, wsStatus]
  );
  const doctorOpenAlerts = dashboard?.total_open_alerts ?? 0;

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
    let reconnectTimer = null;
    let heartbeatTimer = null;
    let closedByCleanup = false;
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${window.location.host}/ws/doctor?token=${encodeURIComponent(token)}`);
    setWsStatus("connecting");
    ws.onopen = () => {
      setWsStatus("connected");
      ws.send("connected");
      heartbeatTimer = window.setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send("ping");
      }, 20000);
    };
    ws.onmessage = (e) => {
      let eventText = e.data;
      try {
        const parsed = JSON.parse(e.data);
        eventText = `${parsed.type || "EVENT"} | Alert ${parsed.alert_id ?? "-"} | ${
          parsed.severity || parsed.status || "INFO"
        } | ${parsed.message || ""}`;
        if (parsed.type === "ALERT_CREATED" && role === "DOCTOR") {
          loadDoctorDashboard().catch(() => {});
        }
      } catch {
        eventText = String(e.data);
      }
      setEvents((prev) => [`[${new Date().toLocaleTimeString()}] ${eventText}`, ...prev].slice(0, 120));
    };
    ws.onerror = () => setWsStatus("error");
    ws.onclose = () => {
      if (closedByCleanup) return;
      setWsStatus("disconnected");
      reconnectTimer = window.setTimeout(() => setWsSeed((v) => v + 1), 1800);
    };
    return () => {
      closedByCleanup = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      if (heartbeatTimer) window.clearInterval(heartbeatTimer);
      ws.close();
    };
  }, [token, role, wsSeed]);

  useEffect(() => {
    if (!token || !["ADMIN", "DOCTOR", "SIMULATOR"].includes(role)) return;
    loadPatientList().catch(() => {});
  }, [token, role]);

  useEffect(() => {
    if (!token || role !== "DOCTOR") return;
    loadDoctorDashboard().catch(() => {});
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
      api(`/v1/patients/${targetPatientId}/vitals?limit=${chartRange}`, { token }),
      api(`/v1/patients/${targetPatientId}/stats?limit=${chartRange}`, { token }),
    ]);
    setPoints(series.points || []);
    setStats(summary.summary || {});
    setPatientId(targetPatientId);
  };

  const loadPredictions = async (targetPatientId = patientId) => {
    const data = await api(`/v1/patients/${targetPatientId}/predictions?limit=120`, { token });
    setPredictionRows(Array.isArray(data) ? data : []);
    pushOutput({ predictions_loaded: Array.isArray(data) ? data.length : 0, patient_id: targetPatientId });
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

  const loadQueueHealth = async () => {
    const data = await api("/v1/queue/health", { token });
    setQueueHealth(data);
    pushOutput(data);
  };

  const loadAudit = async () => {
    const data = await api("/v1/audit", { token });
    setAuditEntries(data || []);
    pushOutput({ audit_entries_loaded: data?.length || 0 });
  };

  const retryFailedEvents = async () => {
    const data = await api("/v1/failed-events/retry", { method: "POST", token });
    setFailedRetrySummary(data);
    pushOutput(data);
  };

  const sendChatMessage = async () => {
    if (!chatInput.trim()) return;
    try {
      setChatSending(true);
      const body = {
        message: chatInput.trim(),
        patient_id: Number(chatPatientId) || null,
      };
      const data = await api("/v1/chatbot/message", { method: "POST", token, body });
      const userMessage = chatInput.trim();
      setChatHistory((prev) =>
        [
          ...prev,
          {
            user: userMessage,
            reply: data.reply,
            risk_level: data.risk_level,
            strategy_used: data.strategy_used || "unknown",
            ts: new Date().toISOString(),
          },
        ].slice(-30)
      );
      if (!chatOpen) setChatUnread((v) => v + 1);
      setChatInput("");
      pushOutput(data);
    } catch (e) {
      pushOutput(String(e));
    } finally {
      setChatSending(false);
    }
  };

  const acknowledgeAlert = async (alertId) => {
    try {
      setAckInFlightId(alertId);
      const data = await api(`/v1/alerts/${alertId}/ack`, { method: "POST", token, body: {} });
      pushOutput(data);
      await loadDoctorDashboard();
      await loadNotifications();
      flashAckNotice(`Alert ${alertId} acknowledged successfully`);
    } catch (e) {
      pushOutput(String(e));
      flashAckNotice(`Failed to acknowledge alert ${alertId}`);
    } finally {
      setAckInFlightId(null);
    }
  };

  const reviewRequest = async (requestId, approve) => {
    const path = `/v1/admin/caregiver-requests/${requestId}/${approve ? "approve" : "reject"}`;
    await api(path, { method: "POST", token, body: { notes: approve ? "Approved in portal" : "Rejected in portal" } });
    await loadRequests();
  };

  const runAction = (action) => async () => {
    try {
      await action();
    } catch (e) {
      pushOutput(String(e));
    }
  };

  const scrollToRef = (targetRef) => {
    if (!targetRef?.current) return;
    targetRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const loadPredictionsAndFocus = async (targetPatientId = patientId) => {
    setPredictionsPanelOpen(true);
    await loadPredictions(targetPatientId);
    window.setTimeout(() => scrollToRef(predictionsSectionRef), 80);
  };

  const reconnectWebSocket = () => {
    setWsStatus("reconnecting");
    setWsSeed((v) => v + 1);
  };

  const flashAckNotice = (message) => {
    if (ackNoticeTimerRef.current) window.clearTimeout(ackNoticeTimerRef.current);
    setAckNotice(message);
    ackNoticeTimerRef.current = window.setTimeout(() => setAckNotice(""), 1500);
  };

  useEffect(() => {
    return () => {
      if (ackNoticeTimerRef.current) window.clearTimeout(ackNoticeTimerRef.current);
    };
  }, []);

  if (!token) {
    return <AuthPage onLogin={onLogin} signup={signup} setSignup={setSignup} onSignup={onSignup} authError={authError} />;
  }

  return (
    <div className="app-shell">
      <HeaderBar role={role} wsStatus={wsStatus} onReconnect={reconnectWebSocket} onLogout={logout} />

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
                    <button onClick={runAction(loadDoctorDashboard)}>Load Alerts Snapshot</button>
                    <button onClick={runAction(loadRequests)}>Load Relative Requests</button>
                    <button onClick={runAction(() => loadPatientInsights(patientId))}>Load Patient Charts</button>
                    <button onClick={runAction(() => loadPredictionsAndFocus(patientId))}>Load AI Predictions</button>
                    <button onClick={runAction(loadPatientList)}>Refresh Patient IDs</button>
                    <button onClick={runAction(loadSystemMetrics)}>Load NFR Metrics</button>
                    <button onClick={runAction(loadNotifications)}>Load Notifications</button>
                    <button onClick={runAction(loadQueueHealth)}>Load Queue Health</button>
                    <button onClick={runAction(loadAudit)}>Load Audit Log</button>
                    <button onClick={runAction(retryFailedEvents)}>Retry Failed Events</button>
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
                  <h3>Admin Operations Center</h3>
                  <div className="stats-grid">
                    <StatCard label="Open Alerts" value={dashboard?.total_open_alerts} />
                    <StatCard label="Samples" value={stats.samples} />
                    <StatCard label="Avg Heart Rate" value={stats.heart_rate_avg} />
                    <StatCard label="Min SpO2" value={stats.spo2_min} />
                    <StatCard label="Ingestion RPS (1m)" value={systemMetrics?.ingestion?.readings_per_second_last_minute} />
                    <StatCard label="Outbox Pending" value={systemMetrics?.outbox?.pending} />
                    <StatCard label="Queue Messages" value={systemMetrics?.queue?.messages} />
                    <StatCard label="Outbox p95(s)" value={systemMetrics?.outbox?.publish_latency_p95_s_last_hour} />
                    <StatCard label="Queue Connected" value={queueHealth?.connected ? "YES" : "NO"} />
                    <StatCard label="Queue DLQ Messages" value={queueHealth?.dlq_messages} />
                    <StatCard label="Retry Resolved" value={failedRetrySummary?.resolved} />
                    <StatCard label="Retry Exhausted" value={failedRetrySummary?.exhausted} />
                  </div>
                  <h4>Recent Audit Entries</h4>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Audit ID</th>
                          <th>Action</th>
                          <th>Actor</th>
                          <th>Target</th>
                          <th>At</th>
                        </tr>
                      </thead>
                      <tbody>
                        {auditEntries.slice(0, 20).map((entry) => (
                          <tr key={entry.id}>
                            <td>{entry.id}</td>
                            <td>{entry.action}</td>
                            <td>{entry.actor}</td>
                            <td>{entry.target || "-"}</td>
                            <td>{entry.created_at ? formatTimeLabel(entry.created_at) : "-"}</td>
                          </tr>
                        ))}
                        {!auditEntries.length && (
                          <tr>
                            <td colSpan={5} className="muted">
                              No audit data loaded yet.
                            </td>
                          </tr>
                        )}
                      </tbody>
                    </table>
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
              <section className="panel-grid doctor-grid">
                <article className="panel doctor-panel">
                  <h3>{doctorWorkspaceTitle}</h3>
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
                    <button onClick={runAction(sendOneReading)}>Send One Reading</button>
                    <button onClick={() => setAutoMode((s) => !s)}>{autoMode ? "Stop Auto Mode" : "Start Auto Mode"}</button>
                    <button onClick={runAction(loadDoctorDashboard)}>Load Alert Dashboard</button>
                    <button onClick={runAction(loadPatientList)}>Refresh Patient IDs</button>
                    <button onClick={runAction(() => loadPatientInsights(patientId))}>Load Charts</button>
                    <button onClick={runAction(() => loadPredictionsAndFocus(patientId))}>Load AI Predictions</button>
                    <button onClick={runAction(loadNotifications)}>Load Notifications</button>
                  </div>
                </article>
                <article className="panel doctor-side-panel">
                  <h3>Doctor Monitoring Hub • Open Alerts: {doctorOpenAlerts}</h3>
                  <div className="stats-grid">
                    <StatCard label="Open Alerts" value={dashboard?.total_open_alerts} />
                    <StatCard label="Samples" value={stats.samples} />
                    <StatCard label="Max Temp" value={stats.temperature_max} />
                    <StatCard label="Latest Time" value={stats.latest_ts ? formatTimeLabel(stats.latest_ts) : "-"} />
                  </div>
                  <div className="section-toggle-row">
                    <h4>Open Alerts (Acknowledge)</h4>
                    <button className="secondary" onClick={() => setAlertsPanelOpen((v) => !v)}>
                      {alertsPanelOpen ? "Close Alerts" : "Open Alerts"}
                    </button>
                  </div>
                  {alertsPanelOpen && <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Alert ID</th>
                          <th>Patient</th>
                          <th>Severity</th>
                          <th>Message</th>
                          <th>Status</th>
                          <th>Action</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(dashboard?.active_alerts || []).slice(0, 40).map((alert) => (
                          <tr key={alert.id}>
                            <td>{alert.id}</td>
                            <td>{alert.patient_id}</td>
                            <td>{alert.severity}</td>
                            <td>{alert.message}</td>
                            <td>{alert.status}</td>
                            <td>
                              {alert.status === "OPEN" ? (
                                <button disabled={ackInFlightId === alert.id} onClick={() => acknowledgeAlert(alert.id)}>
                                  {ackInFlightId === alert.id ? "Acknowledging..." : "Acknowledge"}
                                </button>
                              ) : (
                                <span className="muted">-</span>
                              )}
                            </td>
                          </tr>
                        ))}
                        {!dashboard?.active_alerts?.length && (
                          <tr>
                            <td colSpan={6} className="muted">
                              No open alerts. Generate vitals or refresh dashboard.
                            </td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>}
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
                    <button onClick={runAction(loadPatientPortal)}>Load My Portal</button>
                    <button onClick={runAction(() => loadPatientInsights(patientId))}>Refresh Charts</button>
                    <button onClick={runAction(() => loadPredictionsAndFocus(patientId))}>Load AI Predictions</button>
                    <button onClick={runAction(loadNotifications)}>Load Notifications</button>
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
                    <button onClick={runAction(loadRelativeDash)}>Load Assigned Patients</button>
                    <button onClick={runAction(() => loadPatientInsights(patientId))}>Load Selected Patient Charts</button>
                    <button onClick={runAction(() => loadPredictionsAndFocus(patientId))}>Load AI Predictions</button>
                    <button onClick={runAction(loadNotifications)}>Load Notifications</button>
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
                    <button onClick={runAction(loadPatientList)}>Refresh Patient IDs</button>
                    <button onClick={runAction(loadSystemMetrics)}>Load NFR Metrics</button>
                    <button onClick={runAction(() => loadPatientInsights(patientId))}>Load Charts</button>
                    <button onClick={runAction(() => loadPredictionsAndFocus(patientId))}>Load AI Predictions</button>
                    <button onClick={runAction(loadNotifications)}>Load Notifications</button>
                  </div>
                </article>
                <article className="panel">
                  <h3>Simulator Control Hub</h3>
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
        <article className="panel" ref={chartsSectionRef}>
          <div className="section-toggle-row">
            <h3>Data Visualizations</h3>
            <button className="secondary" onClick={() => setChartsPanelOpen((v) => !v)}>
              {chartsPanelOpen ? "Close Charts" : "Open Charts"}
            </button>
          </div>
          {chartsPanelOpen && (
            <>
          <div className="row">
            <label>
              Graph Range
              <select value={chartRange} onChange={(e) => setChartRange(Number(e.target.value))}>
                <option value={30}>Last 30 points</option>
                <option value={60}>Last 60 points</option>
                <option value={120}>Last 120 points</option>
                <option value={240}>Last 240 points</option>
              </select>
            </label>
            <button onClick={runAction(() => loadPatientInsights(patientId))}>Apply Range To Charts</button>
          </div>
          <VitalLineChart points={visiblePoints} dataKey="heart_rate" label="Heart Rate Trend" unit="bpm" color="#2a7fff" />
          <VitalLineChart points={visiblePoints} dataKey="spo2" label="SpO2 Trend" unit="%" color="#00a37a" />
          <VitalLineChart points={visiblePoints} dataKey="temperature" label="Temperature Trend" unit="C" color="#ff5f5f" />
            </>
          )}
          <div className="section-toggle-row" ref={predictionsSectionRef}>
            <h4>AI Predictions</h4>
            <button className="secondary" onClick={() => setPredictionsPanelOpen((v) => !v)}>
              {predictionsPanelOpen ? "Close AI Predictions" : "Open AI Predictions"}
            </button>
          </div>
          {predictionsPanelOpen && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Prediction ID</th>
                    <th>Patient</th>
                    <th>Combined Severity</th>
                    <th>EWS Severity</th>
                    <th>Baseline Severity</th>
                    <th>Predicted At</th>
                  </tr>
                </thead>
                <tbody>
                  {visiblePredictionRows.map((p) => (
                    <tr key={p.id || `${p.patient_id}-${p.created_at}`}>
                      <td>{p.id ?? "-"}</td>
                      <td>{p.patient_id ?? "-"}</td>
                      <td>{p.combined_severity ?? "-"}</td>
                      <td>{p.ews_severity ?? "-"}</td>
                      <td>{p.baseline_severity ?? "-"}</td>
                      <td>{(p.predicted_at || p.created_at || p.ts) ? formatTimeLabel(p.predicted_at || p.created_at || p.ts) : "-"}</td>
                    </tr>
                  ))}
                  {!visiblePredictionRows.length && (
                    <tr>
                      <td colSpan={6} className="muted">
                        No prediction records loaded. Click "Load AI Predictions".
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
          <p className="muted">
            Note: <strong>EWS</strong> means Early Warning Score (threshold-based clinical risk). <strong>Baseline</strong> means personalized drift from that patient's normal pattern.
          </p>
        </article>
        <article className="panel" ref={notificationsSectionRef}>
          <div className="section-toggle-row">
            <h3>Notifications</h3>
            <button className="secondary" onClick={() => setNotificationsPanelOpen((v) => !v)}>
              {notificationsPanelOpen ? "Close Notifications" : "Open Notifications"}
            </button>
          </div>
          <div className="row">
            <button onClick={runAction(loadNotifications)}>Refresh Notifications</button>
          </div>
          {notificationsPanelOpen && <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Notification ID</th>
                  <th>Alert ID</th>
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
          </div>}

          <h3>Realtime Events</h3>
          <pre>{events.join("\n") || "No websocket events yet"}</pre>
          <h3>System Output</h3>
          <pre>{output}</pre>
        </article>
      </section>

      <button
        type="button"
        className="chat-fab"
        onClick={() => {
          setChatOpen((v) => !v);
          setChatUnread(0);
        }}
      >
        Chatbot
        {chatUnread > 0 ? <span className="chat-unread">{chatUnread}</span> : null}
      </button>

      {chatOpen && (
        <section className="chat-popup">
          <header className="chat-popup-header">
            <div>
              <strong>VitalTrack Live Chat</strong>
              <p className="muted">Advisory triage assistant (non-diagnostic)</p>
            </div>
            <button className="secondary" onClick={() => setChatOpen(false)}>
              Close
            </button>
          </header>
          <div className="chat-popup-controls">
            <label>
              Patient ID
              <input type="number" value={chatPatientId} onChange={(e) => setChatPatientId(Number(e.target.value) || 1)} />
            </label>
          </div>
          <div className="chat-popup-body">
            {chatHistory.length ? (
              chatHistory.map((c, idx) => (
                <div key={`${c.ts}-${idx}`} className="chat-msg">
                  <p>
                    <strong>You:</strong> {c.user}
                  </p>
                  <p>
                    <strong>Bot ({c.risk_level}):</strong> {c.reply}
                  </p>
                  <p className="muted">
                    Provider: {String(c.strategy_used || "unknown").toUpperCase()}
                  </p>
                  <span className="muted">{formatTimeLabel(c.ts)}</span>
                </div>
              ))
            ) : (
              <p className="muted">No chatbot messages yet.</p>
            )}
          </div>
          <div className="chat-popup-input">
            <input
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              placeholder="Type message and press Enter..."
              onKeyDown={(e) => {
                if (e.key === "Enter") sendChatMessage();
              }}
            />
            <button onClick={sendChatMessage} disabled={chatSending}>
              {chatSending ? "Sending..." : "Send"}
            </button>
          </div>
        </section>
      )}
      {ackNotice ? <div className="ack-toast">{ackNotice}</div> : null}
    </div>
  );
}
