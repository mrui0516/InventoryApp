"""The shelf map screen: one grid of model x colour, tapped to change state.

Kept out of views.py because it shares nothing with the product catalogue -
no photos, no prices, no barcodes. The whole page answers two questions: do we
have one, and is there more above the shelf.
"""
import json

from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from .models import (Brand, CaseStock, CaseStyle, DeviceModel, ModelShelfNote,
                     ShelfColour, natural_key)
from .models.shelf import NEXT_STATE, OUT, STOCK_STATES
from .permissions import has_manager_access


def _state_map(style):
    """``{(model_id, colour_id): state}`` for one style, in a single query.

    Rows are created lazily - a model x colour with no row has never been
    stocked, which reads the same as OUT - so the grid must not assume one
    exists for every cell.
    """
    return {
        (row.model_id, row.colour_id): row.state
        for row in CaseStock.objects.filter(style=style).only(
            'model_id', 'colour_id', 'state')
    }


def _grid(style):
    """Rows of ``{model, note, cells}``, brands together, numbers in order."""
    states = _state_map(style)
    notes = {n.model_id: n.note
             for n in ModelShelfNote.objects.filter(style=style)}
    colours = list(ShelfColour.objects.filter(is_active=True))
    models = (DeviceModel.objects
              .filter(is_active=True)
              .select_related('brand')
              .order_by('brand__name', 'sort_key', 'name'))

    rows = []
    for model in models:
        cells = [{
            'colour': colour,
            'state': states.get((model.id, colour.id), OUT),
        } for colour in colours]
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
    return colours, rows


@login_required
def shelf_view(request, slug=None):
    """The grid for one case style."""
    styles = list(CaseStyle.objects.filter(is_active=True))
    if not styles:
        return render(request, 'stock/shelf.html', {'styles': [], 'style': None})

    style = (get_object_or_404(CaseStyle, slug=slug, is_active=True)
             if slug else styles[0])
    colours, rows = _grid(style)

    return render(request, 'stock/shelf.html', {
        'styles': styles,
        'style': style,
        'colours': colours,
        'rows': rows,
        'brands': Brand.objects.order_by('name'),
        'can_edit_catalog': has_manager_access(request.user),
        'state_cycle_json': json.dumps(NEXT_STATE),
        'state_labels_json': json.dumps(dict(STOCK_STATES)),
        'total_models': len(rows),
        'out_models': sum(1 for row in rows if not row['in_stock']),
    })


@login_required
@require_POST
def shelf_set_state(request):
    """Change one cell. Any signed-in user: marking a colour sold out is the
    job of whoever is standing at the shelf, not a manager task."""
    try:
        style_id = int(request.POST['style'])
        model_id = int(request.POST['model'])
        colour_id = int(request.POST['colour'])
    except (KeyError, TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Bad request.'}, status=400)

    row, _created = CaseStock.objects.get_or_create(
        style_id=style_id, model_id=model_id, colour_id=colour_id)

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

    model = DeviceModel.objects.create(brand=brand, name=name)
    return JsonResponse({'ok': True, 'id': model.id, 'name': model.name,
                         'brand': brand.name, 'sort_key': model.sort_key})


@login_required
@require_POST
def shelf_add_colour(request):
    """Add a colour the shop has started stocking."""
    if not has_manager_access(request.user):
        return JsonResponse({'ok': False, 'error': 'Managers only.'}, status=403)

    name = (request.POST.get('name') or '').strip()
    if not name:
        return JsonResponse({'ok': False, 'error': 'Give the colour a name.'}, status=400)

    existing = ShelfColour.objects.filter(name__iexact=name).first()
    if existing is not None:
        if not existing.is_active:          # bringing a retired colour back
            existing.is_active = True
            existing.save(update_fields=['is_active'])
            return JsonResponse({'ok': True, 'id': existing.id, 'name': existing.name})
        return JsonResponse({'ok': False, 'error': f'"{existing.name}" already exists.',
                             'id': existing.id}, status=409)

    last = ShelfColour.objects.order_by('-sort_order').first()
    colour = ShelfColour.objects.create(
        name=name,
        swatch=(request.POST.get('swatch') or '').strip()[:7],
        sort_order=(last.sort_order + 1) if last else 1)
    return JsonResponse({'ok': True, 'id': colour.id, 'name': colour.name,
                         'swatch': colour.swatch})


@login_required
@require_POST
def shelf_add_style(request):
    """Add a case style - clear TPU, MagSafe, and so on."""
    if not has_manager_access(request.user):
        return JsonResponse({'ok': False, 'error': 'Managers only.'}, status=403)

    name = (request.POST.get('name') or '').strip()
    if not name:
        return JsonResponse({'ok': False, 'error': 'Give the style a name.'}, status=400)

    slug = slugify(name)[:60]
    existing = CaseStyle.objects.filter(slug=slug).first()
    if existing is not None:
        return redirect('shelf_style', slug=existing.slug)

    last = CaseStyle.objects.order_by('-sort_order').first()
    style = CaseStyle.objects.create(
        name=name, slug=slug, sort_order=(last.sort_order + 1) if last else 1)
    return redirect('shelf_style', slug=style.slug)
