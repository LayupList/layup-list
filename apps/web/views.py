import sys
from django.shortcuts import render, redirect
from django.conf import settings
from django.views.decorators.http import require_safe, require_POST
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    HttpResponseNotFound,
    HttpResponseRedirect,
    JsonResponse,
)
from django.core.urlresolvers import reverse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Count
from apps.recommendations.models import Recommendation
from apps.web.models import (
    Course,
    CourseMedian,
    DistributiveRequirement,
    Review,
    Student,
    Vote,
)
from apps.web.models.forms import ReviewForm, SignupForm
from lib.grades import numeric_value_for_grade
from lib.terms import numeric_value_of_term
from lib.departments import get_department_name
from lib import constants

LIMITS = {
    "courses": 20,
    "reviews": 5,
    "unauthenticated_review_search": 3,
}


@require_safe
def landing(request):
    return render(request, 'landing.html', {
        'page_javascript': 'LayupList.Web.Landing()',
        'review_count': Review.objects.count(),
    })


def signup(request):
    if request.method == 'POST':
        form = SignupForm(request.POST)
        if form.is_valid():
            form.save_and_send_confirmation(request)
            return render(request, 'instructions.html')
        else:
            return render(request, 'signup.html', {"form": form})

    else:
        return render(request, 'signup.html', {"form": SignupForm()})


def auth_login(request):
    if request.method == 'POST':
        username = request.POST.get('email').lower().split('@')[0]
        password = request.POST.get('password')
        next_url = request.GET.get('next', '/layups')

        user = authenticate(username=username, password=password)
        if user is not None:
            if user.is_active:
                login(request, user)
                return redirect(next_url)
            else:
                return render(request, 'login.html', {
                    "error": (
                        "Please activate your account via the activation link "
                        "first.")
                })
        else:
            return render(request, 'login.html', {"error": "Invalid login."})
    elif request.method == 'GET':
        return render(request, 'login.html')
    else:
        return render(request, 'login.html', {
            "error": "Please authenticate."
        })


@login_required
def auth_logout(request):
    logout(request)
    return render(request, 'logout.html')


@require_safe
def confirmation(request):
    link = request.GET.get('link')

    if link:
        try:
            student = Student.objects.get(confirmation_link=link)
        except Student.DoesNotExist:
            return render(request, 'confirmation.html', {
                'error': 'Confirmation code expired or does not exist.'
            })

        if student.user.is_active:
            return render(request, 'confirmation.html', {
                'already_confirmed': True
            })

        student.user.is_active = True
        student.user.save()
        return render(request, 'confirmation.html', {
            'already_confirmed': False
        })


@require_safe
def current_term(request, sort):
    if sort == "best":
        course_type, primary_sort, secondary_sort = (
            "Best Classes", "-quality_score", "-difficulty_score")
        vote_category = Vote.CATEGORIES.QUALITY
    else:
        if not request.user.is_authenticated():
            return HttpResponseRedirect(
                reverse("signup") + "?restriction=see layups")

        course_type, primary_sort, secondary_sort = (
            "Layups", "-difficulty_score", "-quality_score")
        vote_category = Vote.CATEGORIES.DIFFICULTY

    dist = request.GET.get('dist')
    dist = dist.upper() if dist else dist
    term_courses = Course.objects.for_term(
        constants.CURRENT_TERM, dist
    ).prefetch_related(
        'distribs', 'review_set', 'courseoffering_set'
    ).order_by(primary_sort, secondary_sort)

    paginator = Paginator(term_courses, LIMITS["courses"])
    try:
        courses = paginator.page(request.GET.get('page'))
    except PageNotAnInteger:
        courses = paginator.page(1)
    except EmptyPage:
        courses = paginator.page(paginator.num_pages)

    if courses.number > 1 and not request.user.is_authenticated():
        return HttpResponseRedirect(
            reverse("signup") + "?restriction=see more")

    for_layups_js_boolean = str(sort != "best").lower()

    courses_and_votes = Vote.objects.authenticated_group_courses_with_votes(
        courses.object_list, vote_category, request.user
    )

    return render(request, 'current_term.html', {
        'term': constants.CURRENT_TERM,
        'sort': sort,
        'course_type': course_type,
        'courses': courses,
        'courses_and_votes': courses_and_votes,
        'distribs': DistributiveRequirement.objects.all(),
        'page_javascript': 'LayupList.Web.CurrentTerm({})'.format(
            for_layups_js_boolean)
    })


def course_detail(request, course_id):
    try:
        course = Course.objects.get(pk=course_id)
    except Course.DoesNotExist:
        return HttpResponseNotFound('<h1>Page not found</h1>')

    form = None
    if (request.user.is_authenticated() and
            Review.objects.user_can_write_review(request.user.id, course_id)):
        if request.method == 'POST':
            form = ReviewForm(request.POST)
            if form.is_valid():
                review = form.save(commit=False)
                review.course = course
                review.user = request.user
                review.save()
                form = None  # don't show form again after successful submit
        else:
            form = ReviewForm()

    paginator = Paginator(
        course.review_set.all().order_by("-term"), LIMITS["reviews"])
    try:
        reviews = paginator.page(request.GET.get('page'))
    except PageNotAnInteger:
        reviews = paginator.page(1)
    except EmptyPage:
        reviews = paginator.page(paginator.num_pages)

    if request.user.is_authenticated():
        difficulty_vote, quality_vote = Vote.objects.for_course_and_user(
            course, request.user)
    else:
        difficulty_vote, quality_vote = None, None

    similarity_recommendations = course.recommendations.filter(
        creator=Recommendation.DOCUMENT_SIMILARITY,
    ).order_by('-weight').prefetch_related('recommendation')

    professors_and_review_count = (
        course.review_set.values("professor")
                         .annotate(Count("professor"))
                         .order_by("-professor__count")
                         .values_list("professor", "professor__count"))

    return render(request, 'course_detail.html', {
        'term': constants.CURRENT_TERM,
        'course': course,
        'last_offered': course.last_offered(),
        'recommendations': similarity_recommendations,
        'professors_and_review_count': professors_and_review_count,
        'difficulty_vote': difficulty_vote,
        'quality_vote': quality_vote,
        'reviews': reviews,
        'distribs': course.distribs_string(),
        'xlist': course.crosslisted_courses.all(),
        'review_form': form,
        'page_javascript': 'LayupList.Web.CourseDetail({})'.format(course_id)
    })


@require_safe
def departments(request):
    department_codes_and_counts = (
        Course.objects.filter(number__lt=100)  # Undergraduate courses
                      .exclude(department='RAD')
                      .values('department')
                      .annotate(Count('department'))
                      .order_by('department')
                      .values_list('department', 'department__count'))
    return render(request, 'departments.html', {
        'departments': [
            (code, get_department_name(code), count)
            for code, count in department_codes_and_counts
        ],
    })


@require_safe
def course_search(request):
    query = request.GET.get("q", "").strip()
    if len(query) < 3:
        return render(request, 'course_search.html', {
            'query': query,
            'courses': []
        })
    courses = Course.objects.search(query).prefetch_related(
        'review_set', 'courseoffering_set', 'distribs')
    if len(courses) == 1:
        return redirect(courses[0])

    if len(query) not in Course.objects.DEPARTMENT_LENGTHS:
        courses = sorted(
            courses, key=lambda c: c.review_set.count(), reverse=True)

    return render(request, 'course_search.html', {
        'term': constants.CURRENT_TERM,
        'query': query,
        'department': get_department_name(query),
        'courses': courses,
    })


@require_safe
def course_review_search(request, course_id):
    if not request.user.is_authenticated():
        return HttpResponseRedirect(
            reverse("signup") + "?restriction=see reviews")

    query = request.GET.get("q", "").strip()
    course = Course.objects.get(id=course_id)
    reviews = course.search_reviews(query)
    review_count = reviews.count()

    if not request.user.is_authenticated():
        reviews = reviews[:LIMITS["unauthenticated_review_search"]]

    return render(request, 'course_review_search.html', {
        'query': query,
        'course': course,
        'reviews_full_count': review_count,
        'remaining': review_count - LIMITS["unauthenticated_review_search"],
        'reviews': reviews,
        'page_javascript': 'LayupList.Web.CourseReviewSearch()'
    })


@require_safe
def medians(request, course_id):

    # retrieve course medians for term, and group by term for averaging
    medians_by_term = {}
    for course_median in CourseMedian.objects.filter(course=course_id):
        if course_median.term not in medians_by_term:
            medians_by_term[course_median.term] = []

        medians_by_term[course_median.term].append({
            'median': course_median.median,
            'enrollment': course_median.enrollment,
            'section': course_median.section,
            'numeric_value': numeric_value_for_grade(course_median.median)
        })

    return JsonResponse({'medians': sorted(
        [
            {
                'term': term,
                'avg_numeric_value': sum(
                    m['numeric_value'] for m in term_medians
                ) / len(term_medians),
                'courses': term_medians,
            } for term, term_medians in medians_by_term.iteritems()
        ],
        key=lambda x: numeric_value_of_term(x['term']),
        reverse=True,
    )})


@require_safe
def course_professors(request, course_id):
    return JsonResponse({
        'professors': list(Review.objects.filter(
            course=course_id
        ).values_list('professor', flat=True).distinct().order_by('professor'))
    })


@require_POST
def vote(request, course_id):
    if not request.user.is_authenticated():
        return HttpResponseForbidden()

    try:
        value = request.POST["value"]
        forLayup = request.POST["forLayup"] == "true"
    except KeyError:
        return HttpResponseBadRequest()

    category = Vote.CATEGORIES.DIFFICULTY if forLayup else Vote.CATEGORIES.QUALITY
    new_score, is_unvote = Vote.objects.vote(
        int(value), course_id, category, request.user)

    return JsonResponse({
        'new_score': new_score,
        'was_unvote': is_unvote
    })
