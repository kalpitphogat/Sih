import { Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import Home from './pages/Home'
import Simulation from './pages/Simulation'
import RealtimeMonitoring from './pages/RealtimeMonitoring'
import About from './pages/About'

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/simulation" element={<Simulation />} />
        <Route path="/monitoring" element={<RealtimeMonitoring />} />
        <Route path="/about" element={<About />} />
      </Routes>
    </Layout>
  )
}
