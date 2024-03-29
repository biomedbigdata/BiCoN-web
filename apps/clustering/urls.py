from django.urls import path

from .views import IndexView, AnalysisSetupView, submit_analysis, analysis_status, analysis_status_single, poll_status, \
    analysis_result, \
    results, bookmark, DocumentationView, AboutView, test, test_result

app_name = 'clustering'
urlpatterns = [
    # Index Page
    path('', IndexView.as_view(), name='index'),

    # Analysis setup page (overview page / first page)
    path('analysis/', AnalysisSetupView.as_view(), name='analysis_setup'),
    # Only for post-requests. If called via head, just redirect to status
    path('analysis/submit', submit_analysis, name='submit_job'),
    # Page to monitor current status of job
    path('analysis/status/<uuid:analysis_id>', analysis_status_single, name='analysis_status_single'),
    # Page to monitor current status of all the recent jobs
    path('analysis/status/', analysis_status, name='analysis_status'),
    # Url for polling the status of one task
    path('analysis/status/poll', poll_status, name='poll_status'),
    # Page for looking at one specific result
    path('analysis/result/<uuid:analysis_id>', analysis_result, name='analysis_result'),
    # Page for all the results
    path('analysis/results', results, name='analysis_results'),

    # TEST PAGE
    path('analysis/test_result', test_result, name='test_result'),
    path('analysis/test', test, name='test'),

    # Page to restore a session (bookmark a session)
    path('bookmark/<uuid:session_id>', bookmark, name='bookmark'),
    # Page for documentation and source
    path('documentation/', DocumentationView.as_view(), name='documentation'),
    path('about/', AboutView.as_view(), name='about'),
]
