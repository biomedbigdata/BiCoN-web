import datetime
import json
import re
import uuid

import pandas as pd
from celery.result import AsyncResult
from celery.states import SUCCESS, FAILURE
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView
from importlib_metadata import version

from .models import Job
from .tasks import run_algorithm


class IndexView(TemplateView):
    template_name = "clustering/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['navbar'] = 'home'
        return context


class AnalysisSetupView(TemplateView):
    template_name = "clustering/analysis/analysis_setup.html"

    def get_context_data(self, **kwargs):
        # Create new session if none is found
        if not self.request.session.session_key:
            self.request.session.create()

        context = super().get_context_data(**kwargs)
        context['navbar'] = 'analysis'
        context['groupbar'] = 'setup_analysis'
        return context


def submit_analysis(request):
    """
    Parse the options and files from the given POST request (generated by AnalysisSetupView)
    and start analysis task with Celery
    """
    # For debugging
    print(f'===== POST REQUEST: {request.POST}')
    print(f'===== REQUEST FILES: {request.FILES}')

    # ========== Test if all the parameters are set correctly ==========
    # Check parameters before parsing to gather a complete list of errors
    # error_list contains the error messages if errors are found
    error_list = list()
    post_keys = request.POST.keys()
    file_keys = request.FILES.keys()
    # --- Step 0: Populate session_id
    if not request.session.session_key:
        error_list.append('No session_id found. Please enable cookies in your browser and try again')

    # --- Step 1: Expression Data
    if 'expression-data' not in post_keys:
        error_list.append('No "Gene Expression Data" option selected')

    elif request.POST['expression-data'] == 'custom' and 'expression-data-file' not in file_keys:
        error_list.append('"Custom expression data" selected but no file uploaded')

    # --- Step 2: PPI Network
    if 'ppi-network' not in post_keys:
        error_list.append('No "PPI-Network" option selected')

    elif request.POST['ppi-network'] == 'custom' and 'ppi-network-file' not in file_keys:
        error_list.append('"Custom PPI-Network" selected but no file uploaded')

    # --- Step 3: Meta data
    if 'use_metadata' in post_keys:
        if 'survival-col' not in post_keys:
            error_list.append('"Survival column name" missing')
        elif request.POST['use_metadata'] == 'yes':
            if not request.POST['survival-col'].strip():
                error_list.append('"Survival column name" is empty')
            if 'survival-metadata-file' not in file_keys:
                error_list.append('"Use additional metadata" selected but no "Survival metadata file" not uploaded')

    # --- Step 4: Algorithm parameters (Required)
    if 'L_g_min' not in post_keys:
        error_list.append('"L_g_min" not specified')
    else:
        tmp_var = request.POST['L_g_min']
        try:
            int(tmp_var)
        except (ValueError, TypeError):
            error_list.append('"L_g_min" cannot be empty and must be a number')

    if 'L_g_max' not in post_keys:
        error_list.append('"L_g_max" not specified')
    else:
        tmp_var = request.POST['L_g_max']
        try:
            int(tmp_var)
        except (ValueError, TypeError):
            error_list.append('"L_g_max" cannot be empty and must be a number')

    # --- Step 5: Job name (Optional)
    if 'job_name' not in post_keys:
        error_list.append('"job_name" not specified')

    else:
        tmp_var = request.POST['job_name']
        reg = re.compile(r'^[a-zA-Z0-9-_\s]+$')
        if not reg.match(tmp_var) and tmp_var:
            error_list.append('"Job name" contains not allowed characters')

        if len(tmp_var) > 30:
            error_list.append('""Job name" is longer than 30 characters')


    # ========== Parse algorithm parameters from post request ==========
    session_id = None
    algorithm_parameters = dict()

    # --- Step 0: Populate session_id
    if request.user.is_authenticated:
        pass  # Add user auth here and set session_id to user
    else:
        session_id = request.session.session_key

    # --- Step 1: Expression Data
    expr_data_selection = request.POST['expression-data']
    expr_data_str = None
    apply_log2 = False
    apply_z_transformation = False

    # Parse expression network from uploaded file into string (easier to serialize than file object)
    if expr_data_selection == 'custom':
        try:
            expr_data_str = request.FILES['expression-data-file'].read().decode('utf-8')
        except UnicodeDecodeError:
            error_list.append("The expression data file is not a plain text file (cannot be decoded in utf-8)")

        apply_log2 = 'expression-data-log2' in request.POST.keys()
        apply_z_transformation = 'z-score-transformation' in request.POST.keys()

    # --- Step 2: PPI Network
    ppi_network_selection = request.POST['ppi-network']
    ppi_network_str = None

    # Parse ppi network from uploaded file into string (easier to serialize than file object)
    if ppi_network_selection == 'custom':
        try:
            ppi_network_str = request.FILES['ppi-network-file'].read().decode('utf-8')

        except UnicodeDecodeError:
            error_list.append("The ppi network file is not a plain text file (cannot be decoded in utf-8)")

    # --- Step 3: Meta data
    survival_col_name = None
    clinical_df = None
    use_metadata = False

    if 'use_metadata' in request.POST:
        if request.POST['use_metadata'] == 'yes':  # Set parameter for metadata analysis if desired
            print("USING METADATA")
            use_metadata = True
            if 'survival-col' in request.POST and 'survival-metadata-file' in request.FILES:
                survival_col_name = request.POST['survival-col'].strip()
                clinical_df = pd.read_csv(request.FILES['survival-metadata-file'])
        else:  # Set the variables to empty, default checkbox for example data
            pass

    algorithm_parameters['use_metadata'] = use_metadata
    algorithm_parameters['survival_col_name'] = survival_col_name
    algorithm_parameters['clinical_df'] = clinical_df

    # --- Step 4: Algorithm parameters (Required)
    L_g_min = int(request.POST['L_g_min'])
    L_g_max = int(request.POST['L_g_max'])

    # --- Step 4.1: Algorithm parameters (Optional)
    if request.POST['clustering_advanced'] == 'yes':
        nbr_iter = int(request.POST.get("nbr_iter"))
        gene_set_size = int(request.POST.get("gene_set_size", 2000))
        nbr_ants = int(request.POST.get("nbr_ants", 30))
        evap = float(request.POST.get("evap", 0.3))
        pher_sig = float(request.POST.get("pher", 1))
        hi_sig = float(request.POST.get("hisig", 1))
        epsilon = float(request.POST.get("stopcr", 0.02))

        algorithm_parameters['max_iter'] = nbr_iter
        algorithm_parameters['size'] = gene_set_size
        algorithm_parameters['k'] = nbr_ants
        algorithm_parameters['evaporation'] = evap
        algorithm_parameters['b'] = pher_sig
        algorithm_parameters['a'] = hi_sig
        algorithm_parameters['eps'] = epsilon

    # If the error list is not empty, redirect back to analysis setup and display errors
    # Todo redirect back to analysis setup instead of displaying on submit...
    if error_list:
        print("Input was incorrect. Redirecting back to setup page")
        return render(request, "clustering/analysis/analysis_setup.html", context={
            'navbar': 'analysis',
            'groupbar': 'setup_analysis',
            'error_list': error_list
        })

    print('All the given data was parsed: Starting clustering')

    # --- Step 4.2: Job name (Optional)
    job_name = request.POST['job_name'].strip()

    # ========== Save the task into the database (create a job) ==========
    task_id = uuid.uuid4()

    if session_id:
        job = Job(job_id=task_id, session_id=session_id, submit_time=timezone.now(),
                  status=Job.SUBMITTED, job_name=job_name)
        job.save()
    else:
        raise KeyError('session_id not found')

    # ========== Run the clustering algorithm ==========
    print('Running algorithm started')
    # started_algorithm_id = run_algorithm.delay(job, expr_data_selection, expr_data_str, ppi_network_selection,
    #                                            ppi_network_str, False, gene_set_size, L_g_min, L_g_max, n_proc=1,
    #                                            a=hi_sig, b=pher_sig, K=nbr_ants, evaporation=evap, th=0.5, eps=epsilon,
    #                                            times=6, clusters=2, cost_limit=5, max_iter=nbr_iter, opt=None,
    #                                            show_pher=False, show_plot=False, save=None, show_nets=False).id

    run_algorithm.apply_async(args=[job, expr_data_selection, expr_data_str, ppi_network_selection, ppi_network_str,
                                    L_g_min, L_g_max, apply_log2, apply_z_transformation],
                              kwargs=algorithm_parameters,
                              task_id=str(task_id), ignore_result=True)

    print(f'redicreting to analysis_status')
    return HttpResponseRedirect(reverse('clustering:analysis_status_single', kwargs={'analysis_id': task_id}))


def test(request):
    old_key = request.session.session_key
    request.session.cycle_key()
    return HttpResponse(f'old key {old_key} : new key {request.session.session_key}')


def test_result(request):
    # return render(request, 'clustering/result_single.html', context={
    #     'ppi_json': 'clustering/test/ppi.json',
    #     'heatmap_png': 'clustering/test/heatmap.png',
    #     'survival_plotly': 'clustering/test/output_plotly.html',
    #     'convergence_png': 'clustering/test/conv.png',
    # })
    pass


def poll_status(request):
    data = 'Internal failure. Please contact your administrator'
    if request.is_ajax():
        if 'task_id' in request.POST.keys() and request.POST['task_id']:
            # Retrieve task and get details
            task_id = request.POST['task_id']
            task = AsyncResult(task_id)
            task_info = task.info
            task_status = task.status
            # Create dictionary for response
            if task_status == FAILURE or task_status == SUCCESS:
                data = dict()
            else:
                data = task_info

            data['task_status'] = task_status
        else:
            data = 'No task_id in the request found'
    else:
        data = 'This is not an ajax request'

    json_data = json.dumps(data)
    return HttpResponse(json_data, content_type='application/json')


def bookmark(request, session_id):
    if not 'sessionid' in request.session.keys:
        request.session.create()

    request.session.session_key = session_id
    return HttpResponse(request.session.session_key)


def analysis_status(request):
    session_id = request.session.session_key
    running_jobs = Job.objects.filter(session_id__exact=session_id).exclude(
        finished_time__lt=(timezone.now() - datetime.timedelta(minutes=20))).order_by('-submit_time')

    return render(request, 'clustering/analysis/analysis_status.html', context={
        'navbar': 'analysis',
        'groupbar': 'submitted_analysis',
        'previous_jobs': running_jobs
    })


def analysis_status_single(request, analysis_id):
    analysis_task = AsyncResult(str(analysis_id))
    return render(request, 'clustering/analysis/analysis_status_single.html', context={
        'navbar': 'analysis',
        'groupbar': 'single_status',
        'task': analysis_task,
    })


def analysis_result(request, analysis_id):
    job = Job.objects.get(job_id=analysis_id)
    return render(request, 'clustering/analysis/results/result_single.html', context={
        'navbar': 'analysis',
        'groupbar': 'single_result',
        'job_identifier': job.job_name if job.job_name else job.job_id,
        'ppi_png': job.ppi_png.name,
        'ppi_json': job.ppi_json.name,
        'heatmap_png': job.heatmap_png.name,
        'survival_plotly': job.survival_plotly.name,
        'convergence_png': job.convergence_png.name,
        'result_csv': job.result_csv.name,
    })


def results(request):
    session_id = request.session.session_key

    # Get all jobs for this session_id and exclude the running ones
    previous_jobs = Job.objects.filter(session_id__exact=session_id).exclude(status__exact='RUNNING') \
        .order_by('-submit_time')

    return render(request, 'clustering/analysis/results/results.html', context={
        'navbar': 'analysis',
        'groupbar': 'all_results',
        'previous_jobs': previous_jobs
    })


class DocumentationView(TemplateView):
    template_name = 'clustering/documentation.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['navbar'] = 'documentation'
        return context


class AboutView(TemplateView):
    template_name = 'clustering/about.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['navbar'] = 'about'
        context['bicon_version'] = version('bicon')

        return context
