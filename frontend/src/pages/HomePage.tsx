import { Link } from 'react-router-dom'
import './HomePage.css'

function HomePage() {
  return (
    <div className="home-page">
      <h1>RFQ 3D View</h1>
      <p className="home-description">
        Upload engineering drawings and create revolve profiles to extract manufacturing metrics.
      </p>
      <Link to="/jobs/new" className="new-job-button">
        New Job
      </Link>
    </div>
  )
}

export default HomePage








