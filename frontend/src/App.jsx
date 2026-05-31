import React, { useState, useEffect, useRef } from 'react';
import { 
  Tv, Users, TrendingUp, Clock, UserCheck, 
  AlertTriangle, UploadCloud, Film, MapPin, 
  Activity, Play, CheckCircle, RefreshCw, BarChart2 
} from 'lucide-react';
import { 
  BarChart, Bar, XAxis, YAxis, CartesianGrid, 
  Tooltip, ResponsiveContainer, Cell 
} from 'recharts';

const STORE_ID = "STORE_BLR_002";
const HOST_IP = typeof window !== 'undefined' ? window.location.hostname : 'localhost';
const API_BASE_URL = `http://${HOST_IP}:8000`;
const WEBSOCKET_URL = `ws://${HOST_IP}:8000/ws/stream/${STORE_ID}`;

export default function App() {
  // Analytical stats state
  const [metrics, setMetrics] = useState({
    unique_visitors: 0,
    conversion_rate: 0.0,
    avg_dwell_time_seconds: 0.0,
    queue_depth: 0,
    abandonment_rate: 0.0,
    active_visitors: 0
  });

  const [funnel, setFunnel] = useState({
    stages: [
      { stage_name: "1. Store Entry", count: 0, percentage: 100.0, dropoff_percentage: 0 },
      { stage_name: "2. Zone Browsing", count: 0, percentage: 0.0, dropoff_percentage: 0 },
      { stage_name: "3. Queue Joined", count: 0, percentage: 0.0, dropoff_percentage: 0 },
      { stage_name: "4. Checkout Purchase", count: 0, percentage: 0.0, dropoff_percentage: 0 }
    ]
  });

  const [heatmap, setHeatmap] = useState({
    zones: [
      { zone_id: "SKINCARE", visit_frequency: 0, avg_dwell_ms: 0, normalized_value: 0, confidence_flag: "low" },
      { zone_id: "COSMETICS", visit_frequency: 0, avg_dwell_ms: 0, normalized_value: 0, confidence_flag: "low" },
      { zone_id: "BILLING_QUEUE", visit_frequency: 0, avg_dwell_ms: 0, normalized_value: 0, confidence_flag: "low" }
    ]
  });

  const [anomalies, setAnomalies] = useState([]);
  const [health, setHealth] = useState({ status: "healthy", database_connected: true, stale_feed_warnings: [] });

  // Stream state
  const [activeFrame, setActiveFrame] = useState(null);
  const [uploadProgress, setUploadProgress] = useState(null);
  const [isProcessing, setIsProcessing] = useState(false);
  const [processingProgress, setProcessingProgress] = useState(0);
  const [activeVideoId, setActiveVideoId] = useState(null);
  const [recentEvents, setRecentEvents] = useState([]);
  const [wsConnected, setWsConnected] = useState(false);

  const fileInputRef = useRef(null);
  const wsRef = useRef(null);
  const eventIdCache = useRef(new Set());

  // Establish WebSockets lifecycle
  useEffect(() => {
    connectWebSocket();
    triggerSystemReset();
    
    // Poll system health every 8 seconds
    const interval = setInterval(fetchHealthCheck, 8000);
    
    return () => {
      clearInterval(interval);
      if (wsRef.current) wsRef.current.close();
    };
  }, []);

  const triggerSystemReset = async () => {
    try {
      setUploadProgress(null);
      setIsProcessing(false);
      setProcessingProgress(0);
      setActiveFrame(null);
      setRecentEvents([]);
      eventIdCache.current.clear();

      setMetrics({
        unique_visitors: 0,
        conversion_rate: 0.0,
        avg_dwell_time_seconds: 0.0,
        queue_depth: 0,
        abandonment_rate: 0.0,
        active_visitors: 0
      });
      setFunnel({
        stages: [
          { stage_name: "1. Store Entry", count: 0, percentage: 100.0, dropoff_percentage: 0 },
          { stage_name: "2. Zone Browsing", count: 0, percentage: 0.0, dropoff_percentage: 0 },
          { stage_name: "3. Queue Joined", count: 0, percentage: 0.0, dropoff_percentage: 0 },
          { stage_name: "4. Checkout Purchase", count: 0, percentage: 0.0, dropoff_percentage: 0 }
        ]
      });
      setHeatmap({
        zones: [
          { zone_id: "SKINCARE", visit_frequency: 0, avg_dwell_ms: 0, normalized_value: 0, confidence_flag: "low" },
          { zone_id: "COSMETICS", visit_frequency: 0, avg_dwell_ms: 0, normalized_value: 0, confidence_flag: "low" },
          { zone_id: "BILLING_QUEUE", visit_frequency: 0, avg_dwell_ms: 0, normalized_value: 0, confidence_flag: "low" }
        ]
      });
      setAnomalies([]);

      await fetch(`${API_BASE_URL}/video/reset`, { method: "POST" });
    } catch (e) {
      console.warn("Failed to reset system on startup: ", e);
    }
  };

  const connectWebSocket = () => {
    console.log("Connecting to AURA WebSocket stream...");
    const ws = new WebSocket(WEBSOCKET_URL);
    
    ws.onopen = () => {
      console.log("WebSocket connected successfully.");
      setWsConnected(true);
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      
      if (data.type === "live_frame") {
        setActiveFrame(data.frame);
        setProcessingProgress(data.progress);
        setIsProcessing(data.progress < 100.0);
        
        if (data.metrics) setMetrics(data.metrics);
        if (data.funnel) setFunnel(data.funnel);
        if (data.heatmap) setHeatmap(data.heatmap);
        if (data.anomalies) setAnomalies(data.anomalies.anomalies);
        
        if (data.latest_event) {
          addEventLog(data.latest_event);
        }
      } 
      else if (data.type === "metrics_update") {
        if (data.metrics) setMetrics(data.metrics);
        if (data.funnel) setFunnel(data.funnel);
        if (data.heatmap) setHeatmap(data.heatmap);
        if (data.anomalies) setAnomalies(data.anomalies.anomalies);
        if (data.latest_event) addEventLog(data.latest_event);
      } 
      else if (data.type === "processing_completed") {
        setIsProcessing(false);
        setProcessingProgress(100.0);
        fetchStaticData(); // Re-sync final telemetry state
      }
    };

    ws.onclose = () => {
      console.log("WebSocket disconnected. Attempting reconnection...");
      setWsConnected(false);
      setTimeout(connectWebSocket, 4000); // Reconnect backoff
    };

    wsRef.current = ws;
  };

  const addEventLog = (event) => {
    if (eventIdCache.current.has(event.event_id)) return;
    eventIdCache.current.add(event.event_id);
    
    setRecentEvents((prev) => {
      const updated = [event, ...prev];
      return updated.slice(0, 30); // Cap at 30 logs in UI
    });
  };

  const fetchStaticData = async () => {
    try {
      const mRes = await fetch(`${API_BASE_URL}/stores/${STORE_ID}/metrics`);
      if (mRes.ok) setMetrics(await mRes.json());

      const fRes = await fetch(`${API_BASE_URL}/stores/${STORE_ID}/funnel`);
      if (fRes.ok) setFunnel(await fRes.json());

      const hRes = await fetch(`${API_BASE_URL}/stores/${STORE_ID}/heatmap`);
      if (hRes.ok) setHeatmap(await hRes.json());

      const aRes = await fetch(`${API_BASE_URL}/stores/${STORE_ID}/anomalies`);
      if (aRes.ok) {
        const rawA = await aRes.json();
        setAnomalies(rawA.anomalies);
      }
    } catch (e) {
      console.warn("Failed to fetch dashboard data stats: ", e);
    }
  };

  const fetchHealthCheck = async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/health`);
      if (res.ok) setHealth(await res.json());
    } catch (e) {
      setHealth({ status: "degraded", database_connected: false, stale_feed_warnings: ["Server unreachable"] });
    }
  };

  const handleFileUpload = async (event) => {
    const file = event.target.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append("file", file);

    setUploadProgress("Uploading...");
    setIsProcessing(true);
    setProcessingProgress(0);
    setActiveFrame(null);
    setRecentEvents([]);
    eventIdCache.current.clear();

    // Instantly reset local dashboard state to zero-out old metrics
    setMetrics({
      unique_visitors: 0,
      conversion_rate: 0.0,
      avg_dwell_time_seconds: 0.0,
      queue_depth: 0,
      abandonment_rate: 0.0,
      active_visitors: 0
    });
    setFunnel({
      stages: [
        { stage_name: "1. Store Entry", count: 0, percentage: 100.0, dropoff_percentage: 0 },
        { stage_name: "2. Zone Browsing", count: 0, percentage: 0.0, dropoff_percentage: 0 },
        { stage_name: "3. Queue Joined", count: 0, percentage: 0.0, dropoff_percentage: 0 },
        { stage_name: "4. Checkout Purchase", count: 0, percentage: 0.0, dropoff_percentage: 0 }
      ]
    });
    setHeatmap({
      zones: [
        { zone_id: "SKINCARE", visit_frequency: 0, avg_dwell_ms: 0, normalized_value: 0, confidence_flag: "low" },
        { zone_id: "COSMETICS", visit_frequency: 0, avg_dwell_ms: 0, normalized_value: 0, confidence_flag: "low" },
        { zone_id: "BILLING_QUEUE", visit_frequency: 0, avg_dwell_ms: 0, normalized_value: 0, confidence_flag: "low" }
      ]
    });
    setAnomalies([]);

    try {
      const res = await fetch(`${API_BASE_URL}/video/upload`, {
        method: "POST",
        body: formData,
      });

      if (res.ok) {
        const data = await res.json();
        setActiveVideoId(data.video_id);
        setUploadProgress("Complete! Processing...");
      } else {
        setUploadProgress("Upload failed.");
        setIsProcessing(false);
      }
    } catch (e) {
      console.error(e);
      setUploadProgress("Connection error.");
      setIsProcessing(false);
    }
  };

  // Funnel charts design configurations
  const funnelChartData = funnel.stages.map(s => ({
    name: s.stage_name.replace(/^\d+\.\s+/, ''),
    count: s.count,
    percentage: s.percentage
  }));

  const getSeverityColor = (severity) => {
    if (severity === "CRITICAL") return "bg-red-500/10 text-red-400 border-red-500/20";
    if (severity === "WARNING") return "bg-amber-500/10 text-amber-400 border-amber-500/20";
    return "bg-blue-500/10 text-blue-400 border-blue-500/20";
  };

  return (
    <div className="min-h-screen flex flex-col xl:flex-row p-4 gap-4 max-w-[1800px] mx-auto">
      
      {/* LEFT PANEL: UPLOAD AND METRICS CONTROLS */}
      <div className="w-full xl:w-[360px] flex flex-col gap-4 shrink-0">
        
        {/* Branding header */}
        <div className="glass-panel p-5 rounded-2xl flex items-center gap-3">
          <div className="p-2 bg-brand-wood rounded-xl text-brand-cream">
            <Activity className="w-6 h-6 animate-pulse" />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight bg-gradient-to-r from-white to-brand-copper bg-clip-text text-transparent">AURA ANALYTICS</h1>
            <p className="text-xs text-brand-copper font-medium tracking-widest uppercase">AI Retail Intelligence</p>
          </div>
        </div>

        {/* CCTV video upload */}
        <div className="glass-panel p-5 rounded-2xl flex flex-col gap-4">
          <div className="flex justify-between items-center">
            <h2 className="font-semibold text-sm tracking-wider uppercase text-slate-300 flex items-center gap-2">
              <Film className="w-4 h-4 text-brand-copper" /> Video Processor
            </h2>
            {isProcessing && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-brand-copper/20 text-brand-cream animate-pulse border border-brand-copper/30">
                Processing {processingProgress}%
              </span>
            )}
          </div>

          <div 
            onClick={() => fileInputRef.current?.click()}
            className={`border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-all duration-300 ${
              isProcessing 
                ? 'border-brand-copper/20 bg-brand-dark/15 cursor-not-allowed opacity-50' 
                : 'border-slate-700 hover:border-brand-copper/50 hover:bg-brand-dark/30'
            }`}
          >
            <UploadCloud className="w-10 h-10 mx-auto text-slate-400 mb-2" />
            <p className="text-sm font-semibold text-slate-200">Upload CCTV Video</p>
            <p className="text-xs text-slate-500 mt-1">Accepts MP4 store footage</p>
            <input 
              type="file" 
              ref={fileInputRef} 
              onChange={handleFileUpload} 
              disabled={isProcessing}
              className="hidden" 
              accept="video/mp4" 
            />
          </div>

          {uploadProgress && (
            <div className="text-xs font-semibold p-2 bg-slate-900/50 border border-white/5 rounded-lg flex items-center justify-between text-slate-300">
              <span className="flex items-center gap-2">
                <span className="w-1.5 h-1.5 rounded-full bg-brand-copper animate-ping"></span>
                {uploadProgress}
              </span>
              {isProcessing && <span>{processingProgress}%</span>}
            </div>
          )}

          {/* Camera Info HUD */}
          <div className="flex flex-col gap-2 pt-2 border-t border-white/5 text-xs text-slate-400">
            <div className="flex justify-between">
              <span className="flex items-center gap-1"><MapPin className="w-3 h-3 text-brand-copper" /> Active Store</span>
              <span className="font-semibold text-slate-200">{STORE_ID}</span>
            </div>
            <div className="flex justify-between">
              <span className="flex items-center gap-1"><Tv className="w-3 h-3 text-brand-copper" /> Device ID</span>
              <span className="font-semibold text-slate-200">CAM_ENTRY_01</span>
            </div>
            <div className="flex justify-between">
              <span>WebSocket Stream</span>
              <span className={`font-semibold flex items-center gap-1 ${wsConnected ? 'text-emerald-400' : 'text-red-400'}`}>
                <span className={`w-1.5 h-1.5 rounded-full ${wsConnected ? 'bg-emerald-400 pulse-emerald' : 'bg-red-400'}`}></span>
                {wsConnected ? 'Synced' : 'Connecting'}
              </span>
            </div>
          </div>
        </div>

        {/* Live system health check */}
        <div className="glass-panel p-5 rounded-2xl flex flex-col gap-3">
          <div className="flex justify-between items-center">
            <h2 className="font-semibold text-sm tracking-wider uppercase text-slate-300 flex items-center gap-2">
              <RefreshCw className="w-4 h-4 text-brand-copper" /> System Diagnostics
            </h2>
            <span className={`text-[10px] uppercase font-bold px-2 py-0.5 rounded-full ${
              health.status === 'healthy' ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-amber-500/10 text-amber-400 border border-amber-500/20'
            }`}>
              {health.status}
            </span>
          </div>

          <div className="flex flex-col gap-2 text-xs">
            <div className="flex justify-between">
              <span>Database Connected</span>
              <span className={health.database_connected ? "text-emerald-400 font-semibold" : "text-red-400 font-semibold"}>
                {health.database_connected ? "Active" : "Failure"}
              </span>
            </div>
            
            {health.stale_feed_warnings.length > 0 && (
              <div className="mt-2 p-2 bg-amber-500/10 border border-amber-500/20 rounded-lg text-[11px] text-amber-400 leading-normal">
                {health.stale_feed_warnings.map((w, i) => <p key={i}>{w}</p>)}
              </div>
            )}
          </div>
        </div>

      </div>

      {/* RIGHT MAIN PANEL: ANALYTICS VISUALIZER */}
      <div className="flex-1 flex flex-col gap-4">
        


        {/* MIDDLE SECTION: STREAM AND BEHAVIOR FEEDS */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          
          {/* Visual Stream Card (takes 2 cols on lg) */}
          <div className="glass-panel rounded-3xl p-5 lg:col-span-2 flex flex-col gap-4">
            <div className="flex justify-between items-center">
              <h2 className="font-semibold text-slate-300 text-sm tracking-wider uppercase flex items-center gap-2">
                <span className="w-2.5 h-2.5 rounded-full bg-red-500 pulse-emerald"></span> CCTV Analytics Feed
              </h2>
              {isProcessing && (
                <div className="flex items-center gap-2">
                  <div className="w-32 h-1.5 bg-slate-800 rounded-full overflow-hidden">
                    <div className="h-full bg-indigo-500 transition-all duration-300" style={{ width: `${processingProgress}%` }}></div>
                  </div>
                  <span className="text-xs text-slate-400 font-medium">{processingProgress}%</span>
                </div>
              )}
            </div>

            <div className="relative aspect-video bg-slate-900/60 rounded-2xl overflow-hidden border border-white/5 flex items-center justify-center">
              {activeFrame ? (
                <img 
                  src={activeFrame} 
                  alt="AURA live analytics overlay" 
                  className="w-full h-full object-contain"
                />
              ) : (
                <div className="text-center p-8 max-w-sm">
                  <div className="p-4 bg-brand-dark/50 text-brand-copper rounded-full w-fit mx-auto mb-4 border border-brand-copper/20">
                    <Play className="w-8 h-8 mx-auto translate-x-0.5" />
                  </div>
                  <h3 className="text-slate-200 font-bold text-base">Interactive CCTV Monitor</h3>
                  <p className="text-xs text-slate-500 mt-2 leading-relaxed">
                    Upload a shop video file on the sidebar. The YOLOv8 model will track visitors, lines, and zones, streaming processed overrides in real time.
                  </p>
                </div>
              )}
            </div>
          </div>

          {/* Real-time event log */}
          <div className="glass-panel p-5 rounded-3xl flex flex-col gap-4">
            <h2 className="font-semibold text-slate-300 text-sm tracking-wider uppercase flex items-center gap-2">
              <Activity className="w-4 h-4 text-brand-copper" /> Behavioral Pipeline
            </h2>

            <div className="flex-1 overflow-y-auto max-h-[300px] lg:max-h-[380px] flex flex-col gap-2 pr-1">
              {recentEvents.length === 0 ? (
                <div className="h-full flex items-center justify-center text-center p-4">
                  <p className="text-xs text-slate-600 leading-normal">
                    Waiting for retail events...<br />Start processing a video feed to log behavioral telemetry.
                  </p>
                </div>
              ) : (
                recentEvents.map((e) => (
                  <div 
                    key={e.event_id} 
                    className="p-3 bg-slate-900/50 border border-white/5 rounded-xl text-xs flex flex-col gap-1.5 animate-fadeIn"
                  >
                    <div className="flex justify-between items-center">
                      <span className="font-bold text-brand-cream">{e.visitor_id}</span>
                      <span className={`px-2 py-0.5 rounded-full text-[9px] uppercase font-extrabold ${
                        e.event_type === 'PURCHASE' ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/20' :
                        e.event_type.startsWith('ZONE_') ? 'bg-blue-500/20 text-blue-400 border border-blue-500/20' :
                        e.event_type === 'BILLING_QUEUE_JOIN' ? 'bg-amber-500/20 text-amber-400 border border-amber-500/20' :
                        'bg-slate-800 text-slate-300'
                      }`}>
                        {e.event_type.replace('_', ' ')}
                      </span>
                    </div>
                    <div className="text-[11px] text-slate-400 flex justify-between">
                      <span>
                        {e.zone_id && `Zone: ${e.zone_id}`}
                        {e.dwell_ms && ` (Dwell: ${round(e.dwell_ms/1000, 1)}s)`}
                      </span>
                      <span className="text-[10px] text-slate-500">
                        {new Date(e.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                      </span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>

        </div>

        {/* BOTTOM SECTION: FUNNEL AND HEATMAPS */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          
          {/* Funnel analytics */}
          <div className="glass-panel p-5 rounded-3xl lg:col-span-2 flex flex-col gap-4">
            <h2 className="font-semibold text-slate-300 text-sm tracking-wider uppercase flex items-center gap-2">
              <BarChart2 className="w-4 h-4 text-brand-copper" /> Conversion Funnel Analytics
            </h2>

            <div className="h-[220px] w-full">
              {funnel.stages.some(s => s.count > 0) ? (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={funnelChartData} margin={{ top: 20, right: 30, left: 10, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.03)" />
                    <XAxis dataKey="name" stroke="rgba(255,255,255,0.4)" fontSize={11} />
                    <YAxis stroke="rgba(255,255,255,0.4)" fontSize={11} unit="%" />
                    <Tooltip 
                      contentStyle={{ background: '#4B2E2B', border: '1px solid rgba(192, 133, 82, 0.4)', borderRadius: '12px' }}
                      labelStyle={{ color: '#FFF8F0', fontSize: '11px', fontWeight: 'bold' }}
                      itemStyle={{ color: '#FFF8F0', fontSize: '12px' }}
                    />
                    <Bar dataKey="percentage" radius={[8, 8, 0, 0]} barSize={40}>
                      {funnelChartData.map((entry, index) => {
                        const colors = ['#8C5A3C', '#C08552', '#C08552', '#FFF8F0'];
                        return <Cell key={`cell-${index}`} fill={colors[index % colors.length]} />;
                      })}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <div className="h-full flex items-center justify-center text-center">
                  <p className="text-xs text-slate-600">
                    No conversion progression data loaded. Process a video to compile shopper session funnels.
                  </p>
                </div>
              )}
            </div>
          </div>

          {/* Heatmaps Matrix & Dwell Zones */}
          <div className="glass-panel p-5 rounded-3xl flex flex-col gap-4">
            <h2 className="font-semibold text-slate-300 text-sm tracking-wider uppercase flex items-center gap-2">
              <Users className="w-4 h-4 text-brand-copper" /> Zone Engagement Matrix
            </h2>

            <div className="flex-1 flex flex-col gap-3 justify-center">
              {heatmap.zones.map((zone) => (
                <div key={zone.zone_id} className="p-3 bg-slate-900/40 border border-white/5 rounded-2xl flex flex-col gap-1.5">
                  <div className="flex justify-between items-center text-xs">
                    <span className="font-bold text-slate-300">{zone.zone_id}</span>
                    <span className={`text-[10px] px-2 py-0.5 rounded-full font-extrabold uppercase ${
                      zone.confidence_flag === 'high' ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' :
                      zone.confidence_flag === 'medium' ? 'bg-amber-500/10 text-amber-400 border border-amber-500/20' :
                      'bg-slate-800 text-slate-400'
                    }`}>
                      {zone.confidence_flag} data
                    </span>
                  </div>
                  
                  {/* Progress bar normalized value */}
                  <div className="w-full h-2 bg-slate-800 rounded-full overflow-hidden">
                    <div 
                      className={`h-full rounded-full transition-all duration-500 ${
                        zone.zone_id === 'SKINCARE' ? 'bg-brand-wood' :
                        zone.zone_id === 'COSMETICS' ? 'bg-brand-copper' : 'bg-brand-cream'
                      }`}
                      style={{ width: `${zone.normalized_value * 100}%` }}
                    ></div>
                  </div>

                  <div className="flex justify-between text-[10px] text-slate-500">
                    <span>Visits: {zone.visit_frequency}</span>
                    <span>Avg Dwell: {round(zone.avg_dwell_ms/1000, 1)}s</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

        </div>

        {/* BOTTOM FULL-ROW: ANOMALIES & ALERTS */}
        <div className="glass-panel p-5 rounded-3xl flex flex-col gap-4">
          <h2 className="font-semibold text-slate-300 text-sm tracking-wider uppercase flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-amber-500 animate-bounce" /> Live Operational Anomalies & Action Plan
          </h2>

          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
            {anomalies.length === 0 ? (
              <div className="p-4 bg-emerald-500/5 border border-emerald-500/10 rounded-2xl flex items-center gap-3 md:col-span-2 xl:col-span-3">
                <CheckCircle className="w-5 h-5 text-emerald-400" />
                <div>
                  <p className="text-xs font-bold text-slate-200">Zero operational anomalies detected.</p>
                  <p className="text-[10px] text-slate-500 mt-0.5">Store registers are clear, conversion is on-target, and CCTV frame telemetry is healthy.</p>
                </div>
              </div>
            ) : (
              anomalies.map((an) => (
                <div 
                  key={an.anomaly_id} 
                  className={`p-4 border rounded-2xl flex flex-col gap-2 ${getSeverityColor(an.severity)}`}
                >
                  <div className="flex justify-between items-center">
                    <span className="text-[10px] font-extrabold uppercase px-2 py-0.5 bg-black/25 rounded-md tracking-wider">
                      {an.type}
                    </span>
                    <span className="text-[9px] text-slate-500">
                      {new Date(an.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </span>
                  </div>
                  <h4 className="text-xs font-bold text-slate-200 mt-1 leading-snug">{an.description}</h4>
                  <div className="mt-2 pt-2 border-t border-white/5 text-[11px] leading-relaxed text-slate-300">
                    <span className="font-bold block text-slate-200 uppercase text-[9px] tracking-wider mb-0.5 text-indigo-400">Action Plan:</span>
                    {an.suggested_action}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

      </div>

    </div>
  );
}

// Utility rounding helper
function round(value, precision) {
  var multiplier = Math.pow(10, precision || 0);
  return Math.round(value * multiplier) / multiplier;
}
