"""The shelf map screen: one grid of model x option, tapped to change state.

Kept out of views.py because it shares nothing with the product catalogue -
no photos, no prices, no barcodes. The whole page answers two questions: do we
have one, and is there more above the shelf.
"""
import json

from django.contrib.auth.decorators import login_required
from django.db.models import Value
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from .models import (Brand, DeviceModel, ModelShelfNote, ShelfAxis, ShelfOption,
                     ShelfStock, ShelfStyle)
from .models.shelf import NEXT_STATE, OUT, STOCK_STATES
from .permissions import has_manager_access


def _state_map(style):
    """``{(model_id, option_id): state}`` for one style, in a single query.

    Rows are created lazily - a model x option with no row has never been
    stocked, which reads the same as OUT - so the grid must not assume one
    exists for every cell.
    """
    return {
        (row.model_id, row.option_id): row.state
        for row in ShelfStock.objects.filter(style=style).only(
            'model_id', 'option_id', 'state')
    }


def _grid(style):
    """Rows of ``{model, note, cells}``, brands together, numbers in order."""
    states = _state_map(style)
    notes = {n.model_id: n.note
             for n in ModelShelfNote.objects.filter(style=style)}
    columns = list(style.columns())
    # Year first, then the model number. "iPhone X" carries no digits, so a
    # name-only sort drops it below iPhone 16 - the year puts it back between
    # the 8 and the 11, which is where anyone looking down a shelf expects it.
    # A handset with no year sorts last rather than first, so a hand-added one
    # is visibly waiting for a year instead of silently heading the list.
    models = (DeviceModel.objects
              .filter(is_active=True)
              .select_related('brand')
              .annotate(year_order=Coalesce('release_year', Value(9999)))
              .order_by('brand__name', 'year_order', 'sort_key', 'name'))

    rows = []
    for model in models:
        cells = [{
            'option': option,
            'state': states.get((model.id, option.id), OUT),
        } for option in columns]
        rows.append({
            'model': model,
            'note': notes.get(model.id, ''),
            'cells': cells,
            # Everything the live search needs, matched in the browser so
            # typing filters instantly with no round trip.
            'search': ' '.join(filter(None, [
                model.brand.name, model.name, notes.get(model.id, ''),
                ' '.join(model.aliases.values_list('alias', flat=True)),
            ])).lower(),
            'in_stock': any(cell['state'] != OUT for cell in cells),
        })
    return columns, rows


@login_required
def shelf_view(request, slug=None):
    """The grid for one style."""
    styles = list(ShelfStyle.objects.select_related('axis').filter(is_active=True))
    if not styles:
        return render(request, 'stock/shelf.html', {'styles': [], 'style': None})

    style = (get_object_or_404(ShelfStyle, slug=slug, is_active=True)
             if slug else styles[0])
    columns, rows = _grid(style)

    return render(request, 'stock/shelf.html', {
        'styles': styles,
        'style': style,
        'columns': columns,
        'rows': rows,
        'brands': Brand.objects.order_by('name'),
        'axes': ShelfAxis.objects.all(),
        'can_edit_catalog': has_manager_access(request.user),
        'state_cycle_json': json.dumps(NEXT_STATE),
        'state_labels_json': json.dumps(dict(STOCK_STATES)),
        'total_models': len(rows),
        'out_models': sum(1 for row in rows if not row['in_stock']),
    })


@login_required
@require_POST
def shelf_set_state(request):
    """Change one cell. Any signed-in user: marking something sold out is the
    job of whoever is standing at the shelf, not a manager task."""
    try:
        style_id = int(request.POST['style'])
        model_id = int(request.POST['model'])
        option_id = int(request.POST['option'])
    except (KeyError, TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Bad request.'}, status=400)

    row, _created = ShelfStock.objects.get_or_create(
        style_id=style_id, model_id=model_id, option_id=option_id)

    wanted = (request.POST.get('state') or '').strip()
    if wanted and wanted in dict(STOCK_STATES):
        row.state = wanted
        row.updated_by = request.user
        row.save(update_fields=['state', 'updated_at', 'updated_by'])
    else:
        row.cycle(request.user)

    return JsonResponse({'ok': True, 'state': row.state})


@login_required
@require_POST
def shelf_set_note(request):
    """Save the free note against a model - "same case as 14 Pro"."""
    try:
        style_id = int(request.POST['style'])
        model_id = int(request.POST['model'])
    except (KeyError, TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Bad request.'}, status=400)

    note, _created = ModelShelfNote.objects.get_or_create(
        style_id=style_id, model_id=model_id)
    note.note = (request.POST.get('note') or '').strip()[:120]
    note.save(update_fields=['note'])
    return JsonResponse({'ok': True, 'note': note.note})


@login_required
@require_POST
def shelf_add_model(request):
    """Add a handset. It lands in its place: brands together, numbers in
    order, because the ordering is a stored key rather than insertion order."""
    if not has_manager_access(request.user):
        return JsonResponse({'ok': False, 'error': 'Managers only.'}, status=403)

    name = (request.POST.get('name') or '').strip()
    brand_name = (request.POST.get('brand') or '').strip()
    if not name:
        return JsonResponse({'ok': False, 'error': 'Give the model a name.'}, status=400)
    if not brand_name:
        return JsonResponse({'ok': False, 'error': 'Choose a brand.'}, status=400)

    brand, _created = Brand.objects.get_or_create(name__iexact=brand_name,
                                                  defaults={'name': brand_name})
    existing = DeviceModel.objects.filter(brand=brand, name__iexact=name).first()
    if existing is not None:
        return JsonResponse({'ok': False, 'error': f'{existing} already exists.',
                             'id': existing.id}, status=409)

    year = (request.POST.get('release_year') or '').strip()
    model = DeviceModel.objects.create(
        brand=brand, name=name,
        # Almost anything added by hand is a current handset, and a year keeps
        # it in place in the ordering rather than parked at the bottom.
        release_year=int(year) if year.isdigit() else timezone.now().year)
    return JsonResponse({'ok': True, 'id': model.id, 'name': model.name,
                         'brand': brand.name, 'sort_key': model.sort_key})


@login_required
@require_POST
def shelf_add_option(request):
    """Add a column to one axis - a colour, a glue type, whatever it holds."""
    if not has_manager_access(request.user):
        return JsonResponse({'ok': False, 'error': 'Managers only.'}, status=403)

    name = (request.POST.get('name') or '').strip()
    if not name:
        return JsonResponse({'ok': False, 'error': 'Give it a name.'}, status=400)

    axis = ShelfAxis.objects.filter(pk=request.POST.get('axis')).first()
    if axis is None:
        return JsonResponse({'ok': False, 'error': 'Unknown axis.'}, status=400)

    existing = ShelfOption.objects.filter(axis=axis, name__iexact=name).first()
    if existing is not None:
        if not existing.is_active:          # bringing a retired column back
            existing.is_active = True
            existing.save(update_fields=['is_active'])
            return JsonResponse({'ok': True, 'id': existing.id, 'name': existing.name})
        return JsonResponse({'ok': False, 'error': f'"{existing.name}" already exists.',
                             'id': existing.id}, status=409)

    last = ShelfOption.objects.filter(axis=axis).order_by('-sort_order').first()
    option = ShelfOption.objects.create(
        axis=axis, name=name,
        swatch=(request.POST.get('swatch') or '').strip()[:7],
        sort_order=(last.sort_order + 1) if last else 1)
    return JsonResponse({'ok': True, 'id': option.id, 'name': option.name,
                         'swatch': option.swatch})


@login_required
@require_POST
def shelf_add_style(request):
    """Add a style - MagSafe cases, tempered glass, and so on.

    The axis decides what its columns are. Left unsaid it reuses Colour, which
    is right for any case material; glass would pick a glue-and-edge axis.
    """
    if not has_manager_access(request.user):
        return JsonResponse({'ok': False, 'error': 'Managers only.'}, status=403)

    name = (request.POST.get('name') or '').strip()
    if not name:
        return redirect('shelf')

    slug = slugify(name)[:60]
    existing = ShelfStyle.objects.filter(slug=slug).first()
    if existing is not None:
        return redirect('shelf_style', slug=existing.slug)

    axis = ShelfAxis.objects.filter(pk=request.POST.get('axis')).first()
    if axis is None:
        axis, _created = ShelfAxis.objects.get_or_create(
            slug='colour', defaults={'name': 'Colour', 'sort_order': 1})

    last = ShelfStyle.objects.order_by('-sort_order').first()
    style = ShelfStyle.objects.create(
        name=name, slug=slug, axis=axis,
        sort_order=(last.sort_order + 1) if last else 1)
    return redirect('shelf_style', slug=style.slug)
