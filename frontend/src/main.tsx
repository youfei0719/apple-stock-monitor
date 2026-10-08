import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import App from './components/App';
import ErrorBoundary from './components/ErrorBoundary';
import Home from './pages/Home';
import TaskDetail from './pages/TaskDetail';
import AddMonitor from './pages/AddMonitor';
import History from './pages/History';
import Guide from './pages/Guide';
import Me from './pages/Me';
import Login from './pages/Login';
import VerifyEmail from './pages/VerifyEmail';
import './styles/tokens.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        {/* R6-D6：全站 ErrorBoundary——渲染崩溃兜底出错页+重试按钮 */}
        <Route
          element={
            <ErrorBoundary>
              <App />
            </ErrorBoundary>
          }
        >
          <Route path="/login" element={<Login />} />
          <Route path="/verify" element={<VerifyEmail />} />
          <Route path="/" element={<Home />} />
          <Route path="/tasks/:id" element={<TaskDetail />} />
          <Route path="/add" element={<AddMonitor />} />
          <Route path="/history" element={<History />} />
          <Route path="/guide" element={<Guide />} />
          <Route path="/me" element={<Me />} />
          {/* UI-1：未知路径兜底回首页，避免空白 404 死路 */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);
