import { Link } from 'react-router-dom'
import './HomePage.css'

function HomePage() {
  return (
    <div className="home-page">
      <div className="home-contact">
        <p className="home-contact-inline">
          Reach out to us and we'll set up a personalised walkthrough.{' '}
          <a href="mailto:info@quotemycad.com" className="contact-email">
            info@quotemycad.com
          </a>
        </p>
      </div>

      <div className="home-hero">
        <h1>QuoteMyCAD</h1>
        <p className="home-description">
          AI-powered quote automation — upload an engineering drawing and get instant manufacturing metrics.
        </p>
        <Link to="/jobs/new" className="new-job-button">
          New Job
        </Link>
      </div>
    </div>
  )
}

export default HomePage









