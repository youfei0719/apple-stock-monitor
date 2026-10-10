import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Route, Routes } from 'react-router-dom';
import App from './components/App';
import ErrorBoundary from './components/ErrorBoundary';
import Home from './pages/Home';
import TaskDetail from './pages/TaskDetail';
import AddMonitor from './pages/AddMonitor';
import History from './pages/History';
import Guide from './pages/Guide';
import Me from './pages/Me';
import Login from './pages/Login';
import NotFound from './pages/NotFound';
import ResetPassword from './pages/ResetPassword';
import VerifyEmail from './pages/VerifyEmail';
import GroupSettings from './pages/GroupSettings';
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
          <Route path="/reset-password" element={<ResetPassword />} />
          <Route path="/verify" element={<VerifyEmail />} />
          <Route path="/" element={<Home />} />
          <Route path="/tasks/:id" element={<TaskDetail />} />
          <Route path="/group" element={<GroupSettings />} />
          <Route path="/add" element={<AddMonitor />} />
          <Route path="/history" element={<History />} />
          <Route path="/guide" element={<Guide />} />
          <Route path="/me" element={<Me />} />
          {/* UX：未知路径渲染 404 页（标题+回首页），不再静默回首页导致用户无法察觉输错地址 */}
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);
