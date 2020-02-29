from django.shortcuts import render
from django.contrib.auth.models import User, Group
from django import forms
from rest_framework import viewsets
from rest_framework.views import APIView
from rest_framework.response import Response
from django.http import HttpResponse, JsonResponse
from api.serializers import *
from django.core import serializers
from ..models import *
from ..serializers import *
import datetime
from calendar import monthrange
from datetime import datetime
from django.core.paginator import Paginator
import random, string
from django.views.decorators.cache import cache_page
from django.utils.decorators import method_decorator

@method_decorator(cache_page(5), name='dispatch')
class TradeRecentList(APIView):
    def get(self, request):
        #Get pagination data before the request so that it saves memory and is quicker to query.
        page_number = int(self.request.query_params.get("page_number", 1))
        page_size = int(self.request.query_params.get("page_size", 12))

        data = Trade.objects.all().order_by('-date')[(page_number-1)*page_size : page_number*page_size]
        trade_data = data.values()
        
        for idx, trade in enumerate(trade_data):
            buying_party_id = trade.get("buying_party_id")
            selling_party_id = trade.get("selling_party_id")

            #Get the data for the buying and selling party
            #Needs to be done here, cannot be done in the original call recursively because its too slow.
            buying_party_company_data = Company.objects.get(id=buying_party_id)
            buying_company = CompanySerializer(buying_party_company_data)

            selling_party_company_data = Company.objects.get(id=selling_party_id)
            selling_company = CompanySerializer(selling_party_company_data)

            #Add the companies data to the trade
            trade_data[idx]["buying_company"] = buying_company.data.get("name")
            trade_data[idx]["selling_company"] = selling_company.data.get("name")

            #Take the product_id and append meaningful data about this product to the trade
            product_id = trade.get("product_id")
            product_data = Product.objects.get(id=product_id)
            product_s = ProductSerializer(product_data)

            trade_data[idx]["product"] = product_s.data.get("name")

            #print(str(idx) + ":  " + str(trade), end="\n\n")

        #Modified the structure of a trade, will need to use a custom serializer.
        return Response(trade_data)

class RecentTradesByCompanyForProduct(APIView):
    def get(self, request, buyer, product):
        #Get pagination data before the request so that it saves memory and is quicker to query.
        page_number = int(self.request.query_params.get("page_number", 1))
        page_size = int(self.request.query_params.get("page_size", 150))

        data = Trade.objects.filter(buying_party=buyer, product_id=product).order_by('-date')[(page_number-1)*page_size : page_number*page_size]
        
        s = TradeSerializer(data, many=True)
        return Response(s.data)

#Converts a currency from one type, into another at the latest exchange rate
#All must pass "through" dollars as they all derive a dollar value, where a dollar = 1
class CurrencyConversionLatest(APIView):
    def get(self, request, from_currency, to_currency):
        #Check if these currencies actually exist...
        check_from = Currency.objects.filter(currency=from_currency)
        check_to = Currency.objects.filter(currency=to_currency)

        if len(check_from) != 1:
            return JsonResponse(status=400, data={
                "error": "The currency: " + from_currency + " does not exist or has more than 1 result",
                "count": len(check_from)
                })

        if len(check_to) != 1:
            return JsonResponse(status=400, data={
                "error": "The currency: " + to_currency + " does not exist or has more than 1 result.",
                "count": len(check_to)
            })

        #Get the latest currency value of the from currency in USD
        from_data = CurrencyPrice.objects.filter(currency_id=from_currency).order_by("-date")[0]
        to_data = CurrencyPrice.objects.filter(currency_id=to_currency).order_by("-date")[0]

        # if len(from_data) != 1 or len(to_data) != 1:
        #     return JsonResponse(status=500, data={
        #         "error": "Error getting latest currency data",
        #     })
        
        s_from_data = CurrencyPriceSerializer(from_data, many=False)
        s_to_data = CurrencyPriceSerializer(to_data, many=False)

        #Need data to be in 2dp for simplicity
        usd_converted = round(s_from_data.data["value"] / s_to_data.data["value"], 2)

        return JsonResponse(status=200, data={
            "date:": s_from_data.data["date"],
            "from": from_currency,
            "to": to_currency,
            "conversion": usd_converted
        })

class ProductsForSellers(APIView):
    def get(self, id, company):
        #Check the company exists
        check_company = Company.objects.filter(id=company)

        if len(check_company) == 0:
            return JsonResponse(status=400, data={
                "error": "The company does not exist",
                "count": len(check_company)
                })
        
        #Get the products sold by this company
        products_sold = ProductSeller.objects.filter(company_id=company)
        product_data = products_sold.values()
        
        for idx, trade in enumerate(product_data):
            print(trade)
            #Take the product_id and append meaningful data about this product to the trade
            product_id = trade.get("product_id")
            product_data_inner = Product.objects.get(id=product_id)
            product_s = ProductSerializer(product_data_inner)
            print(product_s.data)

            product_data[idx]["name"] = product_s.data.get("name")

        return Response(product_data)

class CurrencyValuesPastMonth(APIView):
    def get(self, request, currency):
        data = CurrencyPrice.objects.filter(currency_id=currency).order_by("-date")[:30]
        s = CurrencyPriceSerializer(data, many=True)
        return Response(s.data)

class CurrencyChanges(APIView):
    def get(self, request):
        currency_rows = dict()
        percentage_change = dict()
        max_date = None
        min_date = None

        for row in Trade.objects.raw("""
        SELECT id, currency_id, value, (SELECT MAX(DATE) FROM currency_price) AS max_date, (DATE_SUB((SELECT MAX(DATE) FROM currency_price), INTERVAL 7 DAY)) AS min_date
        FROM currency_price 
        WHERE DATE BETWEEN DATE_SUB((SELECT MAX(DATE) FROM currency_price), INTERVAL 7 DAY) AND (SELECT MAX(DATE) FROM currency_price) 
        ORDER BY currency_id, DATE DESC;
        """):
            max_date = row.max_date
            min_date = row.min_date
            if row.currency_id not in currency_rows.keys():
                currency_rows[row.currency_id] = list()
                percentage_change[row.currency_id] = 0

            currency_rows[row.currency_id].append(row.value)

        
        for currency in currency_rows:
            start_value = currency_rows[currency][0]
            end_value = currency_rows[currency][-1]
            change = round(end_value / start_value, 5)
            # print(str(currency) + ": " + str(currency_rows[currency]))
            # print("Start: " + str(start_value) + "   |   End: " + str(end_value))
            # print("Change: " + str(change), end="\n\n")
            percentage_change[currency] = change

        percentage_sorted = list({k: v for k, v in sorted(percentage_change.items(), key=lambda item: -item[1])})
        largest_appreciations = percentage_sorted[:5]
        largest_depreciations = percentage_sorted[-5:]
    
        appreciation_dict = list()
        depreciation_dict = list()

        for index, currency in enumerate(largest_appreciations):
            appreciation_dict.append(dict())
            appreciation_dict[index]["currency"] = currency
            appreciation_dict[index]["change"] = str(percentage_change[currency]) + "%"
            appreciation_dict[index]["values"] = currency_rows[currency]
        for index, currency in enumerate(largest_depreciations):
            depreciation_dict.append(dict())
            depreciation_dict[index]["currency"] = currency
            depreciation_dict[index]["change"] = str(percentage_change[currency]) + "%"
            depreciation_dict[index]["values"] = currency_rows[currency]

        print(largest_appreciations)

        return JsonResponse(status=200, data={"max_date": max_date, "min_date": min_date,
            "largest_appreciations": appreciation_dict, "largest_depreciations": depreciation_dict})
