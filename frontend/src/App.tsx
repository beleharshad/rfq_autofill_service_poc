import { BrowserRouter as Router, Routes, Route, Link } from 'react-router-dom'
import HomePage from './pages/HomePage'
import NewJobPage from './pages/NewJobPage'
import JobPage from './pages/JobPage'
import './App.css'

function App() {
  return (
    <Router>
      <div className="app">
        <header className="app-header">
          <Link to="/" className="app-title">
            Mechmind
          </Link>
        </header>
        <main className="app-main">
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/jobs/new" element={<NewJobPage />} />
            <Route path="/jobs/:id" element={<JobPage />} />
          </Routes>
        </main>
      </div>
    </Router>
  )
}

export default App








