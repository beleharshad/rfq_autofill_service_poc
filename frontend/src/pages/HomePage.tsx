import { Link } from 'react-router-dom'
import './HomePage.css'

function HomePage() {
  return (
    <div className="home-page">
      <h1>QuoteMyCAD</h1>
      <p className="home-description">
        AI-powered RFQ autofill — upload an engineering drawing and get instant manufacturing metrics.
      </p>
      <Link to="/jobs/new" className="new-job-button">
        New Job
      </Link>
    </div>
  )
}

export default HomePage








