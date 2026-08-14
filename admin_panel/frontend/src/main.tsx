import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import type {} from './telegram/initData'
import './index.css'

window.Telegram?.WebApp?.ready()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
