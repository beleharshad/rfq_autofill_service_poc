import { Link } from 'react-router-dom'
import './HomePage.css'

function HomePage() {
  return (
    <div className="home-page">
      <div className="home-contact">
        <h2>Request a Demo</h2>
        <p>Interested in integrating QuoteMyCAD into your procurement workflow?</p>
        <p>Reach out to us and we'll set up a personalised walkthrough.</p>
        <a href="mailto:info@quotemycad.com" className="contact-email">
          info@quotemycad.com
        </a>
      </div>

      <div className="home-hero">
        <h1>QuoteMyCAD</h1>
        <p className="home-description">
          AI-powered — upload an engineering drawing and get instant manufacturing metrics.
        </p>
        <Link to="/jobs/new" className="new-job-button">
          New Job
        </Link>
      </div>
    </div>
  )
}

export default HomePage









