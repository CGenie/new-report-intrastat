# -*- encoding: utf-8 -*-
##############################################################################
#
#    Report intrastat base module for Odoo
#    Copyright (C) 2011-2014 Akretion (http://www.akretion.com).
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

from openerp import models, fields


class AccountTax(models.Model):
    _inherit = "account.tax"

    exclude_from_intrastat_if_present = fields.Boolean(
        string='Exclude invoice line from intrastat if this tax is present',
        help="If this tax is present on an invoice line, this invoice "
        "line will be skipped when generating Intrastat Product or "
        "Service lines from invoices.")
