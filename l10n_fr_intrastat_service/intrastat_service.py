# -*- encoding: utf-8 -*-
##############################################################################
#
#    Report intrastat service module for Odoo (DES)
#    Copyright (C) 2010-2014 Akretion (http://www.akretion.com/)
#    @author Alexis de Lattre <alexis.delattre@akretion.com>
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################


from openerp import models, fields, api, _
import openerp.addons.decimal_precision as dp
from openerp.exceptions import Warning
from datetime import datetime
from dateutil.relativedelta import relativedelta
import logging
from lxml import etree

logger = logging.getLogger(__name__)


class L10nFrReportIntrastatService(models.Model):
    _name = "l10n.fr.report.intrastat.service"
    _order = "start_date desc"
    _rec_name = "start_date"
    _inherit = ['mail.thread', 'report.intrastat.common']
    _description = "DES"
    _track = {
        'state': {
            'l10n_fr_intrastat_service.l10n_fr_declaration_done':
            lambda self, cr, uid, obj, ctx=None: obj['state'] == 'done',
            }
        }

    company_id = fields.Many2one(
        'res.company', string='Company',
        required=True, states={'done': [('readonly', True)]},
        default=lambda self: self.env['res.company']._company_default_get(
            'l10n.fr.report.intrastat.service'))
    start_date = fields.Date(
        string='Start date', required=True,
        states={'done': [('readonly', True)]}, copy=False,
        default=lambda self:
        datetime.today() + relativedelta(day=1, months=-1),
        help="Start date of the declaration. Must be the first day of "
        "a month.")
    end_date = fields.Date(
        compute='_compute_dates', string='End date', readonly=True, store=True,
        help="End date for the declaration. Must be the last day of the "
        "month of the start date.")
    year_month = fields.Char(
        compute='_compute_dates', string='Month', readonly=True,
        track_visibility='always', store=True,
        help="Year and month of the declaration.")
    intrastat_line_ids = fields.One2many(
        'l10n.fr.report.intrastat.service.line',
        'parent_id', string='Intrastat Service Lines',
        states={'done': [('readonly', True)]}, copy=False)
    num_lines = fields.Integer(
        compute='_compute_numbers', string='Number of lines', store=True,
        track_visibility='always',
        help="Number of lines in this declaration.")
    total_amount = fields.Float(
        compute='_compute_numbers', digits=dp.get_precision('Account'),
        string='Total amount', store=True, track_visibility='always',
        help="Total amount in company currency of the declaration.")
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True,
        string='Currency')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('done', 'Done'),
        ], string='State', readonly=True, track_visibility='onchange',
        default='draft', copy=False,
        help="State of the declaration. When the state is set to 'Done', "
        "the fields become read-only.")

    _sql_constraints = [(
        'date_uniq', 'unique(start_date, company_id)',
        'A DES for this month already exists !'
        )]

    @api.constrains('start_date')
    def _service_check_start_date(self):
        self._check_start_date()

    @api.multi
    def generate_service_lines(self):
        self.ensure_one()
        line_obj = self.env['l10n.fr.report.intrastat.service.line']
        invoice_obj = self.env['account.invoice']
        self._check_generate_lines()
        # delete all service lines generated from invoices
        lines_to_remove = line_obj.search([
            ('invoice_id', '!=', False),
            ('parent_id', '=', self.id)])
        if lines_to_remove:
            lines_to_remove.unlink()

        invoices = invoice_obj.search([
            ('type', 'in', ('out_invoice', 'out_refund')),
            ('date_invoice', '<=', self.end_date),
            ('date_invoice', '>=', self.start_date),
            ('state', 'in', ('open', 'paid')),
            ('company_id', '=', self.company_id.id)
            ], order='date_invoice')
        for invoice in invoices:
            if not invoice.partner_id.country_id:
                raise Warning(
                    _("Missing country on partner '%s'.")
                    % invoice.partner_id.name)
            elif not invoice.partner_id.country_id.intrastat:
                continue
            elif (invoice.partner_id.country_id.id ==
                    self.company_id.country_id.id):
                continue

            amount_invoice_cur_to_write = 0.0
            amount_company_cur_to_write = 0.0
            amount_invoice_cur_regular_service = 0.0
            amount_invoice_cur_accessory_cost = 0.0
            regular_product_in_invoice = False

            for line in invoice.invoice_line:
                if not line.product_id:
                    continue

                if line.product_id.exclude_from_intrastat:
                    continue

                # If we have a regular product/consu in the invoice, we
                # don't take the is_accessory_cost in DES (it will be in DEB)
                # If we don't, we declare the is_accessory_cost in DES as other
                # regular services
                if line.product_id.type != 'service':
                    regular_product_in_invoice = True
                    continue

                # This check on line.price_subtotal must be AFTER the check
                # on line.product_id.type != 'service' in order to handle
                # the case where we have in an invoice :
                # - some HW products with value = 0
                # - some accessory costs
                # => we want to have the accessory costs in DEB, not in DES
                if not line.quantity or not line.price_subtotal:
                    continue

                skip_this_line = False
                for line_tax in line.invoice_line_tax_id:
                    if line_tax.exclude_from_intrastat_if_present:
                        skip_this_line = True
                if skip_this_line:
                    continue

                if line.product_id.is_accessory_cost:
                    amount_invoice_cur_accessory_cost += line.price_subtotal
                else:
                    amount_invoice_cur_regular_service += line.price_subtotal

            # END of the loop on invoice lines
            if regular_product_in_invoice:
                amount_invoice_cur_to_write = \
                    amount_invoice_cur_regular_service
            else:
                amount_invoice_cur_to_write = (
                    amount_invoice_cur_regular_service
                    + amount_invoice_cur_accessory_cost)

            if invoice.currency_id.name != 'EUR':
                amount_company_cur_to_write = int(round(
                    invoice.currency_id.with_context(
                        date=invoice.date_invoice).compute(
                        amount_invoice_cur_to_write,
                        self.company_id.currency_id, round=False),
                    0))
            else:
                amount_company_cur_to_write = int(round(
                    amount_invoice_cur_to_write, 0))

            if amount_company_cur_to_write:
                if invoice.type == 'out_refund':
                    amount_invoice_cur_to_write = - amount_invoice_cur_to_write
                    amount_company_cur_to_write = - amount_company_cur_to_write

                # Why do I check that the Partner has a VAT number
                # only here and not earlier ? Because, if I sell
                # to a physical person in the EU with VAT, then
                # the corresponding partner will not have a VAT
                # number, and the entry will be skipped because
                # line_tax.exclude_from_intrastat_if_present is
                # always True and amount_company_cur_to_write = 0
                # So we should not block with a raise before the
                # end of the loop on the invoice lines and the "if
                # amount_company_cur_to_write:"
                if not invoice.partner_id.vat:
                    raise Warning(
                        _("Missing VAT number on partner '%s'.")
                        % invoice.partner_id.name)
                else:
                    partner_vat_to_write = invoice.partner_id.vat

                line_obj.create({
                    'parent_id': self.id,
                    'invoice_id': invoice.id,
                    'partner_vat': partner_vat_to_write,
                    'partner_id': invoice.partner_id.id,
                    'invoice_currency_id': invoice.currency_id.id,
                    'amount_invoice_currency': amount_invoice_cur_to_write,
                    'amount_company_currency': amount_company_cur_to_write,
                    })
        return

    @api.one
    def done(self):
        self.state = 'done'
        return

    @api.one
    def back2draft(self):
        self.state = 'draft'
        return

    @api.multi
    def generate_xml(self):
        self.ensure_one()
        start_date_str = self.start_date
        start_date_datetime = fields.Date.from_string(start_date_str)

        self._check_generate_xml()

        my_company_vat = self.company_id.partner_id.vat.replace(' ', '')

        # Tech spec of XML export are available here :
        # https://pro.douane.gouv.fr/download/downloadUrl.asp?file=PubliwebBO/fichiers/DES_DTIPlus.pdf
        root = etree.Element('fichier_des')
        decl = etree.SubElement(root, 'declaration_des')
        num_des = etree.SubElement(decl, 'num_des')
        num_des.text = datetime.strftime(start_date_datetime, '%Y%m')
        num_tva = etree.SubElement(decl, 'num_tvaFr')
        num_tva.text = my_company_vat
        mois_des = etree.SubElement(decl, 'mois_des')
        mois_des.text = datetime.strftime(start_date_datetime, '%m')
        # month 2 digits
        an_des = etree.SubElement(decl, 'an_des')
        an_des.text = datetime.strftime(start_date_datetime, '%Y')
        line = 0
        # we now go through each service line
        for sline in self.intrastat_line_ids:
            line += 1  # increment line number
            ligne_des = etree.SubElement(decl, 'ligne_des')
            numlin_des = etree.SubElement(ligne_des, 'numlin_des')
            numlin_des.text = str(line)
            valeur = etree.SubElement(ligne_des, 'valeur')
            # We take amount_company_currency, to get amounts in EUR
            valeur.text = str(sline.amount_company_currency)
            partner_des = etree.SubElement(ligne_des, 'partner_des')
            try:
                partner_des.text = sline.partner_vat.replace(' ', '')
            except:
                raise Warning(
                    _("Missing VAT number on partner '%s'.")
                    % sline.partner_id.name)
        xml_string = etree.tostring(
            root, pretty_print=True, encoding='UTF-8', xml_declaration=True)

        # We now validate the XML file against the official XSD
        self._check_xml_schema(
            root, xml_string, 'l10n_fr_intrastat_service/data/des.xsd')
        # Attach the XML file
        attach_id = self._attach_xml_file(xml_string, 'des')
        return self._open_attach_view(attach_id, title='DES XML file')

    @api.model
    def _scheduler_reminder(self):
        previous_month = datetime.strftime(
            datetime.today() + relativedelta(day=1, months=-1), '%Y-%m')
        # I can't search on [('country_id', '=', ...)]
        # because it is a fields.function not stored and without fnct_search
        companies = self.env['res.company'].search([])
        logger.info('Starting the Intrastat Service reminder')
        for company in companies:
            if company.country_id.code != 'FR':
                continue
            # Check if an intrastat service already exists for month N-1
            intrastats = self.search([
                ('year_month', '=', previous_month),
                ('company_id', '=', company.id)
                ])
            # if it already exists, we don't do anything
            # in the future, we may check the state and send a mail
            # if the state is still in draft ?
            if intrastats:
                logger.info(
                    'An Intrastat Service for month %s already exists for '
                    'company %s'
                    % (previous_month, company.name))
                continue
            else:
                # If not, we create an intrastat.service for month N-1
                intrastat = self.create({'company_id': company.id})
                logger.info(
                    'An Intrastat Service for month %s has been created '
                    'by Odoo for company %s'
                    % (previous_month, company.name))
                # we try to generate the lines
                try:
                    intrastat.generate_service_lines()
                except Warning as e:
                    intrastat = intrastat.with_context(
                        exception=True, error_msg=e)
                # send the reminder email
                intrastat.send_reminder_email(
                    'l10n_fr_intrastat_service.'
                    'intrastat_service_reminder_email_template')
        return True


class L10nFrReportIntrastatServiceLine(models.Model):
    _name = "l10n.fr.report.intrastat.service.line"
    _description = "Intrastat Service Lines"
    _rec_name = "partner_vat"
    _order = 'id'

    parent_id = fields.Many2one(
        'l10n.fr.report.intrastat.service',
        string='Intrastat service ref', ondelete='cascade')
    company_id = fields.Many2one(
        'res.company', related='parent_id.company_id',
        string="Company", readonly=True, store=True)
    company_currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id',
        string="Company currency", readonly=True)
    invoice_id = fields.Many2one(
        'account.invoice', string='Invoice ref', readonly=True)
    date_invoice = fields.Date(
        related='invoice_id.date_invoice',
        string='Invoice date', readonly=True)
    partner_vat = fields.Char(string='Customer VAT')
    partner_id = fields.Many2one(
        'res.partner', 'Partner name', ondelete='restrict')
    amount_company_currency = fields.Integer(
        string='Amount in company currency',
        help="Amount in company currency to write in the declaration. "
        "Amount in company currency = amount in invoice currency "
        "converted to company currency with the rate of the invoice "
        "date and rounded at 0 digits")
    amount_invoice_currency = fields.Float(
        string='Amount in invoice currency',
        digits=dp.get_precision('Account'), readonly=True,
        help="Amount in invoice currency (not rounded)")
    invoice_currency_id = fields.Many2one(
        'res.currency', "Invoice currency", readonly=True)

    @api.onchange('partner_id')
    def partner_on_change(self):
        if self.partner_id and self.partner_id.vat:
            self.partner_vat = self.partner_id.vat
