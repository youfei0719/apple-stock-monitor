import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Route, Routes } from 'react-router-dom';
import App from './components/App';
import Home from './pages/Home';
import TaskDetail from './pages/TaskDetail';
import AddMonitor from './pages/AddMonitor';
import History from './pages/History';
import Guide from './pages/Guide';
import Me from './pages/Me';
import Login from './pages/Login';
import './styles/tokens.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<App />}>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={<Home />} />
          <Route path="/tasks/:id" element={<TaskDetail />} />
          <Route path="/add" element={<AddMonitor />} />
          <Route path="/history" element={<History />} />
          <Route path="/guide" element={<Guide />} />
          <Route path="/me" element={<Me />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);
